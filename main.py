#!/usr/bin/env python

import argparse
import os
import sys

import src.state as state
from src.consts import DEBUG_HINT_ENVVAR
from src.run import run
from src.util import log

__version__ = '0.1.1'

PRINT_PREFIX = """
        ________________________________________
       /                 /                     /
      /   _ __ ___  _   /__  _____  ___       /
     /---| '_ ` _ \| | | \ \/ / _ \/ __|-----/
    /    | | | | | | |_| |>  <  __/ (__     /
   /     |_| |_| |_|\__,_/_/\_\___|\___|   /
  /                /                      /
 /________________/______________________/

          muXec - Multiplexed Exec
"""

PRINT_SUFFIX = """examples:
    muxec -p 2 'ls -la' 'for i in 1 2 3 4 5 ; do date; sleep 1; done'
    find . -type -f -name '*.java' | head | muxec --xargs 'echo'
    cat dockerfiles.txt | muxec --xargs -p 4 --break-on-fail 'docker build -f {} .'
"""


class MuxecArgumentParser(argparse.ArgumentParser):
    def format_help(self):
        return f"{PRINT_PREFIX}\n{super().format_help()}"

    def format_usage(self):
        return f"{PRINT_PREFIX}\n{super().format_usage()}{PRINT_SUFFIX}"


parser = MuxecArgumentParser()


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
    opts = parse_args()
    state.break_on_fail = opts.break_on_fail
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
    log(f"Running {len(commands)} commands with {parallelism} parallelism, terminal is lines={state.full_height}, cols={state.full_width}")
    log(str(commands))

    run(parallelism, commands)


if __name__ == '__main__':
    if DEBUG_HINT_ENVVAR in os.environ:
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
