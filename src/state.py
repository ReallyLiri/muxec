panes = []
total = 0
all_processes_to_rolling_output = {}
completed_processes = set()
failed_processes = set()
exhausted = False
break_on_fail = False
stdScr = None
full_height, full_width = None, None