import os

from src.consts import LOG_FILE_PATH, LOG_HINT_ENVVAR
from src.errors import ReadSmoreError

should_log = os.environ.get(LOG_HINT_ENVVAR) is not None

if should_log and os.path.exists(LOG_FILE_PATH):
    os.remove(LOG_FILE_PATH)


def log(message):
    if not should_log:
        return
    with open(LOG_FILE_PATH, "a") as f:
        f.write(message + "\n")


def _at(lst, i):
    if i >= len(lst):
        raise ReadSmoreError()
    return lst[i]


def _is_number(ch):
    return ch is not None and ord('0') <= ord(ch) <= ord('9')


def _extract_number(text, i):
    start = i
    while _is_number(_at(text, i)):
        i += 1
    param_str = text[start:i]
    param = int(param_str) if param_str else None
    return i, param
