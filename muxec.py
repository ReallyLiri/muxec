#!/usr/bin/env python

import argparse
import curses
import os
import signal
import subprocess
import time
import traceback

import select

STATUS_HEIGHT = 1
stdScr = curses.initscr()
scr_height, scr_width = stdScr.getmaxyx()
panes = []
total = 0
completed_processes = set()
failed_processes = set()
all_processes = []
exhausted = False
log_file = "/tmp/muxec.log"

if os.path.exists(log_file):
    os.remove(log_file)


def _log(message):
    with open(log_file, "a") as f:
        f.write(message + "\n")


def write_row(top_offset):
    _log(f"Adding row at {top_offset}")
    stdScr.addstr(top_offset, 0, '-' * scr_width)


def create_pane(width, top_offset, bottom_offset):
    left_offset = 0
    height = bottom_offset - top_offset + 1
    pad = curses.newpad(height, width)
    pad.scrollok(True)
    coords = [top_offset, left_offset, bottom_offset, width]
    _log(f"Adding pane with coords: {coords}")
    return {
        'pad': pad,
        'height': height,
        'coords': coords
    }


def write_to_pane(pane_num, text):
    pane = panes[pane_num]
    pad = pane['pad']
    y, x = pad.getyx()
    _log(f"Writing '{text[0:-1]}' at {y},{x}")
    pad.addstr(y, x, text)
    view_top = max(y - pane['height'], 0)
    pad.refresh(view_top, 0, *pane['coords'])


def clear_pane(pane_num):
    pane = panes[pane_num]
    pad = pane['pad']
    pad.move(0, 0)
    y, x = pad.getmaxyx()
    for line in range(y):
        pad.addstr(line, 0, " " * x)
    pad.move(0, 0)
    view_top = max(y - pane['height'], 0)
    pad.refresh(view_top, 0, *pane['coords'])


def build_views(num_panes):
    curses.noecho()
    curses.cbreak()
    curses.curs_set(0)

    pane_height = (scr_height - STATUS_HEIGHT) // num_panes - 1

    update_status()

    for i in range(num_panes):
        top_offset = STATUS_HEIGHT + (pane_height + 1) * i
        write_row(top_offset)
        bottom_offset = top_offset + pane_height
        top_offset += 1
        panes.append(create_pane(scr_width, top_offset, bottom_offset))

    stdScr.refresh()


def update_status():
    status = f"Running... {len(completed_processes)} / {total} completed"
    if len(failed_processes) > 0:
        status = f"{status}, {len(failed_processes)} failed"
    stdScr.addstr(0, 0, status + " " * (scr_width - len(status)))
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

    def _try_run_process(pane_num, clear):
        global exhausted
        process = next(gen, None)
        if process is None:
            exhausted = True
            return
        fds = process_fds(process)
        _log(f"started process '{process.args}' ({process.pid}) with fds {fds} on pane {pane_num}")
        all_processes.append(process)
        for fd in fds:
            active_fds.add(fd)
            pane_num_by_fd[fd] = pane_num
            data_template[fd] = bytes()
            process_by_fd[fd] = process
        if clear:
            clear_pane(pane_num)

    def _on_fd_inactive(fd):
        active_fds.remove(fd)
        process = process_by_fd[fd]
        _log(f"fd {fd} of process {process.pid} is inactive")
        if any(check_fd in active_fds for check_fd in process_fds(process)):
            return None
        completed_processes.add(process.pid)
        process.poll()
        exit_code = process.returncode
        _log(f"Marking {pane_num} as free since process {process.pid} completed with code {exit_code}")
        if exit_code != 0:
            failed_processes.add(process.pid)
        update_status()
        return pane_num

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
                    write_to_pane(pane_num_by_fd[fd], data_string)

            for ready_pane in ready_panes:
                _try_run_process(ready_pane, True)

        update_status()

    except KeyboardInterrupt:
        raise


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-p", "--parallelism", type=int, metavar="parallelism",
        help="number of commands to run in parallel",
        default=4
    )

    parser.add_argument(
        "commands", nargs="+", metavar="command",
        help="commands to run. if using args, escape entire command with quotes"
    )

    return parser.parse_args()


def main():
    opts = parse_args()
    commands = opts.commands
    parallelism = opts.parallelism
    _log(f"running {len(commands)} commands with {parallelism} parallelism, terminal is h={scr_height}, w={scr_width}")

    num_panes = parallelism

    global total
    total = len(commands)
    build_views(num_panes)

    failed = False
    try:
        run(commands)
    except KeyboardInterrupt:
        print("interrupted, shutting down...")
        for proc in all_processes:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
        time.sleep(1)
        for proc in all_processes:
            if proc.poll() is None:
                proc.send_signal(signal.SIGKILL)
    except Exception as ex:
        _log(f"failed with exception: {ex}")
        _log('\n'.join(traceback.format_exception(type(ex), ex, ex.__traceback__)))
        failed = True
    finally:
        end()

    if not failed:
        print(f"Completed running {len(all_processes)} processes, {len(failed_processes)} failed")
        for process in all_processes:
            print(f"\tProcess '{process.args}' ({process.pid}) completed with code {process.returncode}")


if __name__ == '__main__':
    main()
