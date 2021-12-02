import curses

import unicodedata

from . import state as state
from .consts import *
from .errors import ReadSmoreError
from .util import log, _at, _extract_number, should_log


def _write_row(top_offset):
    log(f"Writing full row at {top_offset}")
    state.stdScr.addstr(top_offset, 0, '-' * state.full_width)


def _write_column(left_offset):
    log(f"Writing full column at {left_offset}")
    for line in range(state.full_height):
        if line > 0:
            state.stdScr.addstr(line, left_offset, '|')


def _handle_escape_sequence(pad, text, esc_i):
    # see https://en.wikipedia.org/wiki/ANSI_escape_code#CSI_(Control_Sequence_Introducer)_sequences

    if _at(text, esc_i + 1) != CSI_PREFIX:
        return
    yield esc_i + 1

    if _at(text, esc_i + 2) == '?':
        yield esc_i + 2
        esc_i += 1

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

    log(f"Detected escape sequence with command '{command}' and params {param} {param2}")

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
    if not state.is_tty:
        for line in text.split("\n"):
            if line:
                print(f"[{pane_num}] {line.strip()}")
        return

    pane = state.panes[pane_num]
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
                if ord(_at(text, i + 1)) != LF_ORD:
                    pad.addch(ch)
            else:
                if should_log and unicodedata.category(ch)[0] == 'C' and ch_ord != LF_ORD:
                    log(f"Observed unhandled ctrl character: {ch_ord}")
                pad.addch(ch)
        except ReadSmoreError as err:
            err.skip = i - 1
            raise err
    pad.refresh(0, 0, *pane['coords'])


def _fill_blanks_and_reset_cursor(pad):
    y, x = pad.getmaxyx()
    for line in range(y):
        pad.addstr(line, 0, " " * x)
    pad.move(0, 0)


def update_status():
    if not state.is_tty:
        return
    status = f"Running... {len(state.completed_processes)} / {state.total} completed"
    if len(state.failed_processes) > 0:
        status = f"{status}, {len(state.failed_processes)} failed"
    state.stdScr.addstr(0, 0, status + " " * (state.full_width - len(status)))
    state.stdScr.refresh()


def clear_pane(pane_num):
    if not state.is_tty:
        return
    log(f"Clearing {pane_num}")
    pane = state.panes[pane_num]
    pad = pane['pad']
    _fill_blanks_and_reset_cursor(pad)
    pad.refresh(0, 0, *pane['coords'])


def build_views(num_panes):
    if not state.is_tty:
        for i in range(num_panes):
            state.panes.append({})
        return
    state.stdScr = curses.initscr()
    state.full_height, state.full_width = state.stdScr.getmaxyx()
    log(f"Terminal size is lines={state.full_height}, cols={state.full_width}")
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

    pane_height = (state.full_height - STATUS_HEIGHT) // n_rows
    pane_width = state.full_width // n_cols

    log(f"Setting up grid of {n_rows} rows x {n_cols} cols, pane h={pane_height},w={pane_width}")

    for col in range(n_cols):
        if col > 0:
            _write_column(col * pane_width)
    _write_row(1)
    for row in range(n_rows):
        if row > 0:
            _write_row(STATUS_HEIGHT + row * pane_height)
        for col in range(n_cols):
            top_offset = STATUS_HEIGHT + pane_height * row + 1
            left_offset = pane_width * col
            if col > 0:
                left_offset += 1
            state.panes.append(_create_pane(pane_width - 1, pane_height - 1, top_offset, left_offset))

    state.stdScr.refresh()


def _create_pane(width, height, top_offset, left_offset):
    pad = curses.newpad(height, width)
    pad.scrollok(True)
    bottom_offset = top_offset + height
    coords = [top_offset, left_offset, bottom_offset, left_offset + width]
    log(f"Adding pane with coords: {coords} (h={height},w={width})")
    return {
        'pad': pad,
        'height': height,
        'width': width,
        'coords': coords
    }


def end():
    if not state.is_tty:
        return
    curses.nocbreak()
    state.stdScr.keypad(False)
    curses.echo()
    curses.endwin()
    curses.curs_set(1)
