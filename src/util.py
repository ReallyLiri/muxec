import os

from .consts import LOG_FILE_PATH, LOG_HINT_ENVVAR
from .errors import ReadSmoreError

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


class CircularBuffer:

    def __init__(self, size):
        self.buffer = [None] * size
        self.low = 0
        self.high = 0
        self.size = size
        self.count = 0

    def is_empty(self):
        return self.count == 0

    def is_full(self):
        return self.count == self.size

    def __len__(self):
        return self.count

    def add(self, value):
        if self.is_full():
            self.low = (self.low + 1) % self.size
        else:
            self.count += 1
        self.buffer[self.high] = value
        self.high = (self.high + 1) % self.size

    def remove(self):
        if self.count == 0:
            raise Exception("Circular Buffer is empty")
        value = self.buffer[self.low]
        self.low = (self.low + 1) % self.size
        self.count -= 1
        return value

    def __iter__(self):
        idx = self.low
        num = self.count
        while num > 0:
            yield self.buffer[idx]
            idx = (idx + 1) % self.size
            num -= 1

    def __repr__(self):
        if self.is_empty():
            return 'cb:[]'
        return 'cb:[' + ','.join(map(str, self)) + ']'
