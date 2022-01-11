#!/usr/bin/env python

import itertools
import os
import pty
import select
import signal
import subprocess
import sys
import time
import traceback

from .consts import MODE_TTY, MODE_AUTO, MODE_PLAIN
from .errors import BreakOnFailError, ReadSmoreError
from .panes import write_to_pane, clear_pane, update_status, build_views, end
from .state import get_state, reset_state
from .util import log, CircularBuffer

TERM_ENV = os.environ.get("TERM", "linux")

ordinal_id_counter = itertools.count()


def _create_subprocess(command, pipe):
    return subprocess.Popen(
        command,
        shell=True,
        stdout=pipe,
        stderr=pipe,
        close_fds=True,
        env={
            **os.environ,
            "LINES": str(get_state().full_height),
            "COLUMNS": str(get_state().full_width),
            "TERM": TERM_ENV
        }
    )


def _loop_commands(commands):
    def _process_generator():
        for command in commands:
            read_pipe, write_pipe = pty.openpty() if get_state().is_tty else os.pipe()
            yield _create_subprocess(command, write_pipe), read_pipe

    gen = _process_generator()
    pane_num_by_fd = {}
    process_by_fd = {}
    active_fds = set()
    data_template = {}

    def _try_run_process(run_on_pane_num, clear):
        new_process, new_fd = next(gen, (None, 0))
        if new_process is None:
            get_state().exhausted = True
            write_to_pane(run_on_pane_num, "[completed, no more commands to run]")
            return
        get_state().panes[run_on_pane_num]['process_ordinal_id'] = next(ordinal_id_counter)
        log(f"Started process '{new_process.args}' ({new_process.pid}) with fd {new_fd} on pane {run_on_pane_num} (clear={clear})")
        active_fds.add(new_fd)
        pane_num_by_fd[new_fd] = run_on_pane_num
        data_template[new_fd] = bytes()
        process_by_fd[new_fd] = new_process
        get_state().all_processes_to_rolling_output[new_process] = CircularBuffer(16)
        if clear:
            clear_pane(run_on_pane_num)
        write_to_pane(run_on_pane_num, str(new_process.args) + "\n")

    def _on_process_completed(completed_fd, completed_process):
        get_state().completed_processes.add(completed_process.pid)
        exit_code = completed_process.returncode
        ready_pane_num = pane_num_by_fd[completed_fd]
        log(f"Process {completed_process.pid} with fd {completed_fd} at {ready_pane_num} completed with code {exit_code}")
        if exit_code != 0:
            get_state().failed_processes.add(completed_process.pid)
            if get_state().break_on_fail:
                raise BreakOnFailError()
        update_status()
        return ready_pane_num

    for pane_num in range(len(get_state().panes)):
        _try_run_process(pane_num, False)

    while active_fds or not get_state().exhausted:
        ready_fds, _, _ = select.select(active_fds, [], [], 1)
        for fd in ready_fds:
            continue_read = True
            data_string = ""
            while continue_read:
                data = os.read(fd, 1024)
                if data:
                    data_string += data.decode('utf-8')
                    try:
                        write_to_pane(pane_num_by_fd[fd], data_string)
                        continue_read = False
                    except ReadSmoreError as err:
                        data_string = data_string[err.skip:]
                        continue_read = True
            for line in data_string.split("\n"):
                get_state().all_processes_to_rolling_output[process_by_fd[fd]].add(line)

        ready_panes = []
        for fd in active_fds.copy():
            process = process_by_fd[fd]
            if process.poll() is not None:
                active_fds.remove(fd)
                ready_pane = _on_process_completed(fd, process)
                if ready_pane is not None:
                    ready_panes.append(ready_pane)
                continue

        for ready_pane in ready_panes:
            _try_run_process(ready_pane, True)

    update_status()


def run(parallelism, commands, break_on_fail=False, print_mode=MODE_TTY, prefix_timestamp=False):
    reset_state()

    parallelism = min(parallelism, len(commands))

    num_panes = parallelism

    get_state().total = len(commands)
    get_state().break_on_fail = break_on_fail
    get_state().prefix_timestamp = prefix_timestamp

    if print_mode == MODE_AUTO:
        print_mode = MODE_TTY if sys.stdout.isatty() else MODE_PLAIN

    get_state().is_tty = print_mode == MODE_TTY

    broke = False
    error_message = None
    try:
        log(f"Running {len(commands)} commands with {parallelism} parallelism")
        log(str(commands))
        build_views(num_panes)
        _loop_commands(commands)
    except BaseException as ex:
        if isinstance(ex, KeyboardInterrupt):
            print("interrupted, shutting down...")
        elif isinstance(ex, BreakOnFailError):
            print("breaking on failure...")
            broke = True
        else:
            error_message = f"Failed with exception: {ex}" + '\n'.join(traceback.format_exception(type(ex), ex, ex.__traceback__))
        for proc in get_state().all_processes_to_rolling_output.keys():
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
        time.sleep(1)
        for proc in get_state().all_processes_to_rolling_output.keys():
            if proc.poll() is None:
                proc.send_signal(signal.SIGKILL)
        if isinstance(ex, KeyboardInterrupt):
            return False
    finally:
        end()

    try:
        if not error_message:
            if not broke:
                print(f"Completed running {len(get_state().all_processes_to_rolling_output)} processes, {len(get_state().failed_processes)} failed")
            for process in get_state().all_processes_to_rolling_output.keys():
                if process.returncode == 0:
                    print(f"\tProcess '{process.args}' ({process.pid}) completed successfully")
            any_failed = False
            for process, buffer in get_state().all_processes_to_rolling_output.items():
                if process.returncode != 0 and process.returncode is not None:
                    any_failed = True
                    print(f"\tProcess '{process.args}' ({process.pid}) failed with code {process.returncode}")
                    buffer = "\n\t\t".join(buffer).strip()
                    if buffer:
                        print(f"\t\t{buffer}")
            return not any_failed
        else:
            print(error_message)
            return False
    finally:
        reset_state()
