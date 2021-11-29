#!/usr/bin/env python

import argparse
import curses
import os
import signal
import subprocess
import sys
import time
import traceback

import select

STATUS_HEIGHT = 1
stdScr = None
full_height, full_width = None, None
panes = []
total = 0
completed_processes = set()
failed_processes = set()
all_processes_to_stderr = {}
exhausted = False
log_file = "/tmp/muxec.log"
should_log = os.environ.get("MUXEC_LOG") is not None
break_on_fail = False

if should_log and os.path.exists(log_file):
    os.remove(log_file)


def _log(message):
    if not should_log:
        return
    with open(log_file, "a") as f:
        f.write(message + "\n")


class BreakOnFailError(Exception):
    pass


def write_row(top_offset):
    _log(f"Writing full row at {top_offset}")
    stdScr.addstr(top_offset, 0, '-' * full_width)


def write_column(left_offset):
    _log(f"Writing full column at {left_offset}")
    for line in range(full_height):
        if line > 0:
            stdScr.addstr(line, left_offset, '|')


def create_pane(width, height, top_offset, left_offset):
    pad = curses.newpad(height, width)
    pad.scrollok(True)
    bottom_offset = top_offset + height
    coords = [top_offset, left_offset, bottom_offset, left_offset + width]
    _log(f"Adding pane with coords: {coords} (h={height},w={width})")
    return {
        'pad': pad,
        'height': height,
        'width': width,
        'coords': coords
    }


def refresh_pane(pane, y, x):
    view_top = max(y - pane['height'], 0)
    view_left = max(x - pane['width'], 0)
    pad = pane['pad']
    pad.refresh(view_top, view_left, *pane['coords'])


def write_to_pane(pane_num, text):
    pane = panes[pane_num]
    pad = pane['pad']
    y, x = pad.getyx()
    pad.addstr(y, x, text)
    refresh_pane(pane, y, x)


def clear_pane(pane_num):
    pane = panes[pane_num]
    pad = pane['pad']
    pad.move(0, 0)
    y, x = pad.getmaxyx()
    for line in range(y):
        pad.addstr(line, 0, " " * x)
    pad.move(0, 0)
    refresh_pane(pane, y, x)


def build_views(num_panes):
    global stdScr, full_height, full_width
    stdScr = curses.initscr()
    full_height, full_width = stdScr.getmaxyx()
    curses.noecho()
    curses.cbreak()
    curses.curs_set(0)
    update_status()

    n_rows = 1
    n_cols = num_panes
    if num_panes % 3 == 0:
        n_rows = 3
        n_cols = num_panes // 3
    elif num_panes % 2 == 0:
        n_rows = 2
        n_cols = num_panes // 2

    pane_height = (full_height - STATUS_HEIGHT) // n_rows
    pane_width = full_width // n_cols

    _log(f"Setting up grid of {n_rows} rows x {n_cols} cols, pane h={pane_height},w={pane_width}")

    for col in range(n_cols):
        if col > 0:
            write_column(col * pane_width)
    write_row(1)
    for row in range(n_rows):
        if row > 0:
            write_row(STATUS_HEIGHT + row * pane_height)
        for col in range(n_cols):
            top_offset = STATUS_HEIGHT + pane_height * row + 1
            left_offset = pane_width * col
            if col > 0:
                left_offset += 1
            panes.append(create_pane(pane_width - 1, pane_height - 1, top_offset, left_offset))

    stdScr.refresh()


def update_status():
    status = f"Running... {len(completed_processes)} / {total} completed"
    if len(failed_processes) > 0:
        status = f"{status}, {len(failed_processes)} failed"
    stdScr.addstr(0, 0, status + " " * (full_width - len(status)))
    stdScr.refresh()


def end():
    curses.nocbreak()
    stdScr.keypad(False)
    curses.echo()
    curses.endwin()
    curses.curs_set(1)


def process_fds(process):
    return [process.stdout.fileno(), process.stderr.fileno()]


def run(commands):
    global exhausted

    def _process_generator():
        for command in commands:
            yield subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

    gen = _process_generator()
    pane_num_by_fd = {}
    process_by_fd = {}
    active_fds = set()
    data_template = {}

    def _try_run_process(run_on_pane_num, clear):
        global exhausted
        process = next(gen, None)
        if process is None:
            exhausted = True
            clear_pane(run_on_pane_num)
            return
        fds = process_fds(process)
        _log(f"Started process '{process.args}' ({process.pid}) with fds {fds} on pane {run_on_pane_num} (clear={clear})")
        all_processes_to_stderr[process] = ""
        for fd in fds:
            active_fds.add(fd)
            pane_num_by_fd[fd] = run_on_pane_num
            data_template[fd] = bytes()
            process_by_fd[fd] = process
        if clear:
            clear_pane(run_on_pane_num)

    def _on_fd_inactive(fd):
        active_fds.remove(fd)
        process = process_by_fd[fd]
        _log(f"fd {fd} of process {process.pid} is inactive")
        if any(check_fd in active_fds for check_fd in process_fds(process)):
            return None
        completed_processes.add(process.pid)
        process.poll()
        exit_code = process.returncode
        ready_pane_num = pane_num_by_fd[fd]
        _log(f"Process {process.pid} at {ready_pane_num} completed with code {exit_code}")
        if exit_code != 0:
            failed_processes.add(process.pid)
            if break_on_fail:
                raise BreakOnFailError()
        update_status()
        return ready_pane_num

    for pane_num in range(len(panes)):
        _try_run_process(pane_num, False)

    try:
        while active_fds or not exhausted:
            data_by_fd = {}
            timeout = None
            ready_panes = set()
            while True:
                fds_read, _, _ = select.select(active_fds, [], [], timeout)
                timeout = 0
                if fds_read:
                    for fd in fds_read:
                        data = os.read(fd, 1)
                        if data:
                            if fd not in data_by_fd:
                                data_by_fd[fd] = bytes()
                            data_by_fd[fd] += data
                        else:
                            ready_pane = _on_fd_inactive(fd)
                            if ready_pane is not None:
                                ready_panes.add(ready_pane)
                else:
                    break
            for fd, data in data_by_fd.items():
                if data:
                    data_string = data.decode('utf-8')
                    process = process_by_fd[fd]
                    if fd == process.stderr.fileno():
                        all_processes_to_stderr[process] += data_string
                    write_to_pane(pane_num_by_fd[fd], data_string)

            for ready_pane in ready_panes:
                _try_run_process(ready_pane, True)

        update_status()

    except KeyboardInterrupt:
        raise


parser = argparse.ArgumentParser()


def parse_args():
    parser.add_argument(
        "-p", "--parallelism", type=int,
        help="number of commands to run in parallel (default: 4)",
        default=4
    )

    parser.add_argument(
        "commands", nargs="+",
        help="commands to run. if using args, escape entire command with quotes"
    )

    parser.add_argument(
        "-x", "--xargs", default=False, action='store_true',
        help="pipe in standard input as input to the command"
    )

    parser.add_argument(
        "-I", "--replace-str", type=str, default="{}",
        help="when using xargs mode, replace occurrences of replace-str in the command with input, default: {}"
    )

    parser.add_argument(
        "--break-on-fail", default=False, action='store_true',
        help="immediately break whole execution if any command fails"
    )

    return parser.parse_args()


def main():
    global break_on_fail
    opts = parse_args()
    break_on_fail = opts.break_on_fail
    commands = opts.commands

    if opts.xargs:
        base_command = " ".join(commands)
        replace_str = opts.replace_str
        commands = []
        for line in sys.stdin:
            line = line.strip()
            command = f"{base_command} {line}"
            if replace_str in base_command:
                command = base_command.replace(replace_str, line)
            commands.append(command)

    parallelism = min(opts.parallelism, len(commands))
    _log(f"running {len(commands)} commands with {parallelism} parallelism, terminal is h={full_height}, w={full_width}")

    num_panes = parallelism

    global total
    total = len(commands)

    failed = False
    broke = False
    try:
        build_views(num_panes)
        run(commands)
    except Exception as ex:
        if isinstance(ex, KeyboardInterrupt):
            print("interrupted, shutting down...")
        if isinstance(ex, BreakOnFailError):
            print("breaking on failure...")
            broke = True
        else:
            _log(f"failed with exception: {ex}")
            _log('\n'.join(traceback.format_exception(type(ex), ex, ex.__traceback__)))
            failed = True
        for proc in all_processes_to_stderr.keys():
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
        time.sleep(1)
        for proc in all_processes_to_stderr.keys():
            if proc.poll() is None:
                proc.send_signal(signal.SIGKILL)
    finally:
        end()

    if not failed:
        if not broke:
            print(f"Completed running {len(all_processes_to_stderr)} processes, {len(failed_processes)} failed")
        for process, stderr in all_processes_to_stderr.items():
            print(f"\tProcess '{process.args}' ({process.pid}) completed with code {process.returncode}")
            if process.returncode != 0:
                stderr = stderr.strip().replace('\n', '\n\t\t')
                if stderr:
                    print(f"\t\t{stderr}")
    else:
        print("internal error")


if __name__ == '__main__':
    main()
