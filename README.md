# muXec

[![PyPI version](https://badge.fury.io/py/muxec.svg)](https://badge.fury.io/py/muxec)

A command line tool for running multiple commands simultaneously while observing their output

```
        ________________________________________
       /                 /                     /
      /   _ __ ___  _   /__  _____  ___       /
     /---| '_ ` _ \| | | \ \/ / _ \/ __|-----/
    /    | | | | | | |_| |>  <  __/ (__     /
   /     |_| |_| |_|\__,_/_/\_\___|\___|   /
  /                /                      /
 /________________/______________________/

          muXec - Multiplexed Exec
```

To date, the tool has only been tested on `MacOS` and `xterm` with `python3.9`, and may behave differently in untested setups.

## Install

```bash
pip install --upgrade muxec
```

## Usage

```
positional arguments:
  commands              commands to run. if using args, escape entire command with quotes

optional arguments:
  -h, --help            show this help message and exit
  -p PARALLELISM, --parallelism PARALLELISM
                        number of commands to run in parallel (default: 4)
  -x, --xargs           pipe in standard input as input to the command
  -I REPLACE_STR, --replace-str REPLACE_STR
                        when using xargs mode, replace occurrences of replace-str in the command with input, default: {}
  --break-on-fail       immediately break whole execution if any command fails
```

### Examples

```bash
muxec -p 2 'ls -la' 'for i in 1 2 3 4 5 ; do date; sleep 1; done'
```

![gif1](https://i.imgur.com/igo3q6S.gif)

```bash
muxec -p 6 'ls -la' 'for i in 1 2 3 4 5 ; do date; sleep 1; done' 'echo echo' 'sleep 3 ; echo exiting ; exit 1' 'python --version' 'df -h'
```

![gif2](https://i.imgur.com/SDAMLNw.gif)

```bash
cat images.txt | muxec --xargs -p 4 --break-on-fail 'docker pull'
```

![gif3](https://i.imgur.com/PdfOnDp.gif)

## Open Issues

There is still no full support for all control sequences or for colors.

## Development

To enable logging: `export LOG_HINT_ENVVAR=1`. Logs will be written to `/tmp/muxec.log` (file is overwritten on each run).

PyCharm IDE does not provide the required terminal to run `pty`, but you can run the program from regular terminal and attach debugger.

Set up `Python Debug Server` configuration in PyCharm:

![debug](https://i.imgur.com/qNuTNSB.png)

Then set `export DEBUG_HINT_ENVVAR=1` and run the script: `python main.py ...`
