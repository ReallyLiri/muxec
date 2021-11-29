#!/usr/bin/env python

import argparse
import curses
import os
import pty
import signal
import subprocess
import sys
import time
import traceback

import select
import unicodedata

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


class ReadSmoreError(Exception):
    skip = 0


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


BACKSPACE_ORD = 8
ESCAPE_ORD = 27
LF_ORD = 10
CR_ORD = 13
CSI_PREFIX = '['
CSI_PARAM_SEPARATOR = ';'
CSI_CURSOR_UP = 'A'
CSI_CURSOR_DOWN = 'B'
CSI_CURSOR_FORWARD = 'C'
CSI_CURSOR_BACK = 'D'
CSI_CURSOR_POSITION = 'H'
CSI_CURSOR_ERASE_IN_LINE = 'K'
CSI_CURSOR_ERASE_IN_DISPLAY = 'J'


def _at(lst, i):
    if i >= len(lst):
        raise ReadSmoreError()
    return lst[i]


def _is_number(ch):
    if ch is None:
        return False
    return ord('0') <= ord(ch) <= ord('9')


def _extract_number(text, i):
    start = i
    while _is_number(_at(text, i)):
        i += 1
    param_str = text[start:i]
    param = int(param_str) if param_str else None
    return i, param


def _handle_escape_sequence(pad, text, esc_i):
    # see https://en.wikipedia.org/wiki/ANSI_escape_code#CSI_(Control_Sequence_Introducer)_sequences

    if _at(text, esc_i + 1) != CSI_PREFIX:
        return
    yield esc_i + 1

    param_end_index, param = _extract_number(text, esc_i + 2)
    if param is not None:
        yield from range(esc_i + 2, param_end_index + 1)

    command = _at(text, param_end_index)
    param2 = None
    if command == CSI_PARAM_SEPARATOR:
        param2_end_index, param2 = _extract_number(text, param_end_index + 1)
        if param2 is not None:
            yield from range(param_end_index + 1, param2_end_index + 1)
        command = _at(text, param2_end_index)

    _log(f"Detected escape sequence with command '{command}' and params {param} {param2}")

    if command == CSI_CURSOR_ERASE_IN_DISPLAY:
        if param != 2:
            return
        _fill_blanks_and_reset_cursor(pad)
        return

    y, x = pad.getyx()
    my, mx = pad.getmaxyx()

    param = 1 if param is None else param
    param2 = 1 if param2 is None else param2
    if command == CSI_CURSOR_UP:
        y -= param
    elif command == CSI_CURSOR_DOWN:
        y += param
        if ord(_at(text, param_end_index + 1)) == LF_ORD:
            yield param_end_index + 1
    elif command == CSI_CURSOR_FORWARD:
        x += param
    elif command == CSI_CURSOR_BACK:
        x -= param
    elif command == CSI_CURSOR_POSITION:
        y = param - 1
        x = param2 - 1
    else:
        return
    y = max(min(y, my - 1), 0)
    x = max(min(x, mx - 1), 0)
    pad.move(y, x)


def write_to_pane(pane_num, text):
    pane = panes[pane_num]
    pad = pane['pad']
    skip_indexes = set()
    for i, ch in enumerate(text):
        try:
            if i in skip_indexes:
                continue
            ch_ord = ord(ch)
            if ch_ord == BACKSPACE_ORD:
                pad.addstr("\b \b")
            elif ch_ord == ESCAPE_ORD:
                for skip_idx in _handle_escape_sequence(pad, text, i):
                    skip_indexes.add(skip_idx)
            elif ch_ord == CR_ORD:
                # \r is return to start of line, but \r\n should be treated like \n
                if ord(_at(text, i+1)) == LF_ORD:
                    continue
                y, _ = pad.getyx()
                pad.move(y, 0)
            else:
                if should_log and unicodedata.category(ch)[0] == 'C' and ch_ord != LF_ORD:
                    _log(f"Observed unhandled ctrl character: {ch_ord}")
                pad.addch(ch)
            if ch_ord == LF_ORD or ch_ord == CR_ORD:
                pad.refresh(0, 0, *pane['coords'])
        except ReadSmoreError as err:
            err.skip = i - 1
            raise err
    pad.refresh(0, 0, *pane['coords'])


def _fill_blanks_and_reset_cursor(pad):
    y, x = pad.getmaxyx()
    for line in range(y):
        pad.addstr(line, 0, " " * x)
    pad.move(0, 0)


def clear_pane(pane_num):
    _log(f"Clearing {pane_num}")
    pane = panes[pane_num]
    pad = pane['pad']
    _fill_blanks_and_reset_cursor(pad)
    pad.refresh(0, 0, *pane['coords'])


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
            primary, secondary = pty.openpty()
            yield subprocess.Popen(
                command,
                shell=True,
                stdout=secondary,
                stderr=secondary,
                close_fds=True
            ), primary

    gen = _process_generator()
    pane_num_by_fd = {}
    process_by_fd = {}
    active_fds = set()
    data_template = {}

    def _try_run_process(run_on_pane_num, clear):
        global exhausted
        process, fd = next(gen, (None, 0))
        if process is None:
            exhausted = True
            write_to_pane(run_on_pane_num, "[completed, no more commands to run]")
            return
        _log(f"Started process '{process.args}' ({process.pid}) with fd {fd} on pane {run_on_pane_num} (clear={clear})")
        all_processes_to_stderr[process] = ""
        active_fds.add(fd)
        pane_num_by_fd[fd] = run_on_pane_num
        data_template[fd] = bytes()
        process_by_fd[fd] = process
        if clear:
            clear_pane(run_on_pane_num)

    def _on_process_completed(completed_fd, completed_process):
        completed_processes.add(completed_process.pid)
        exit_code = completed_process.returncode
        ready_pane_num = pane_num_by_fd[completed_fd]
        _log(f"Process {completed_process.pid} with fd {completed_fd} at {ready_pane_num} completed with code {exit_code}")
        if exit_code != 0:
            failed_processes.add(completed_process.pid)
            if break_on_fail:
                raise BreakOnFailError()
        update_status()
        return ready_pane_num

    for pane_num in range(len(panes)):
        _try_run_process(pane_num, False)

    while active_fds or not exhausted:
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
    _log(f"Running {len(commands)} commands with {parallelism} parallelism, terminal is h={full_height}, w={full_width}")

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
            _log(f"Failed with exception: {ex}")
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
    if "MUXEC_DEBUG" in os.environ:
        try:
            import pydevd_pycharm

            pydevd_pycharm.settrace('localhost', port=4024, stdoutToServer=True, stderrToServer=True)
        except ModuleNotFoundError:
            print("Please install pydevd_pycharm manually to your venv, copy correct version from PyCharm Debug Configuration")
            print("i.e `(venv) pip install pydevd-pycharm~=211.7142.13")
            exit(1)
        except ConnectionRefusedError:
            print("*** PyCharm remote debugger is not started -- please start it manually and re-run ***")
            exit(1)
    main()
