_state = None


class State:
    def __init__(self):
        self.panes = []
        self.total = 0
        self.all_processes_to_rolling_output = {}
        self.completed_processes = set()
        self.failed_processes = set()
        self.exhausted = False
        self.break_on_fail = False
        self.stdScr = None
        self.full_height, self.full_width = None, None
        self.is_tty = True
        self.prefix_timestamp = False


def get_state():
    global _state
    if not _state:
        _state = State()
    return _state


def reset_state():
    global _state
    _state = None
