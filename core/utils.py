from functools import partial
from collections import OrderedDict
from contextlib import contextmanager
import html
from itertools import count
import os
import signal
import subprocess
import sys
import time
import threading
import traceback

import sublime


MYPY = False
if MYPY:
    from typing import Callable, Dict, Iterator, Tuple, Type


@contextmanager
def print_runtime(message):
    # type: (str) -> Iterator[None]
    start_time = time.perf_counter()
    yield
    end_time = time.perf_counter()
    duration = round((end_time - start_time) * 1000)
    thread_name = threading.current_thread().name[0]
    print('{} took {}ms [{}]'.format(message, duration, thread_name))


def measure_runtime():
    # type: () -> Callable[[str], None]
    start_time = time.perf_counter()

    def print_mark(message):
        # type: (str) -> None
        end_time = time.perf_counter()
        duration = round((end_time - start_time) * 1000)
        thread_name = threading.current_thread().name[0]
        print('{} after {}ms [{}]'.format(message, duration, thread_name))

    return print_mark


class timer:
    def __init__(self):
        self._start_time = time.perf_counter()

    def passed(self, ms):
        # type: (int) -> bool
        cur_time = time.perf_counter()
        duration = (cur_time - self._start_time) * 1000
        return duration > ms


@contextmanager
def eat_but_log_errors(exception=Exception):
    # type: (Type[Exception]) -> Iterator[None]
    try:
        yield
    except exception:
        traceback.print_exc()


def hprint(msg):
    # type: (str) -> None
    """Print help message for e.g. a failed action"""
    # Note this does a plain and boring print. We use it to
    # mark some usages of print throughout the code-base.
    # We later might find better ways to show these help
    # messages to the user.
    print(msg)


def flash(view, message):
    # type: (sublime.View, str) -> None
    """ Flash status message on view's window. """
    window = view.window()
    if window:
        window.status_message(message)


IDS = partial(next, count())  # type: Callable[[], int]  # type: ignore[assignment]
HIDE_POPUP_TIMERS = {}  # type: Dict[sublime.ViewId, int]
POPUPS = {}  # type: Dict[sublime.ViewId, Tuple]
DEFAULT_TIMEOUT = 2500  # [ms]
DEFAULT_STYLE = {
    'background': 'transparent',
    'foreground': 'var(--foreground)'
}


def show_toast(view, message, timeout=DEFAULT_TIMEOUT, style=DEFAULT_STYLE):
    # type: (sublime.View, str, int, Dict[str, str]) -> Callable[[], None]
    """Show a toast popup at the bottom of the view.

    A timeout of -1 makes a "sticky" toast.
    """
    messages_by_line = escape_text(message).splitlines()
    content = style_message("<br />".join(messages_by_line), style)

    # Order can matter here.  If we calc width *after* visible_region we get
    # different results!
    width, _ = view.viewport_extent()
    visible_region = view.visible_region()
    last_row, _ = view.rowcol(visible_region.end())
    line_start = view.text_point(last_row - 4 - len(messages_by_line), 0)

    vid = view.id()
    key = IDS()

    def on_hide(vid, key):
        if HIDE_POPUP_TIMERS.get(vid) == key:
            HIDE_POPUP_TIMERS.pop(vid, None)

    def __hide_popup(vid, key, sink):
        if HIDE_POPUP_TIMERS.get(vid) == key:
            HIDE_POPUP_TIMERS.pop(vid, None)
            sink()

    inner_hide_popup = show_popup(
        view,
        content,
        max_width=width * 2 / 3,
        location=line_start,
        on_hide=partial(on_hide, vid, key)
    )
    HIDE_POPUP_TIMERS[vid] = key

    hide_popup = partial(__hide_popup, vid, key, inner_hide_popup)
    if timeout > 0:
        sublime.set_timeout(hide_popup, timeout)
    return hide_popup


def show_popup(view, content, max_width, location, on_hide=None):
    vid = view.id()
    inner_hide_popup = view.hide_popup
    actual_key = (int(max_width), location)
    if POPUPS.get(vid) == actual_key:
        view.update_popup(content)
    else:
        def __on_hide(vid, key):
            if POPUPS.get(vid) == key:
                POPUPS.pop(vid, None)
            if on_hide:
                on_hide()

        view.show_popup(
            content,
            max_width=max_width,
            location=location,
            on_hide=partial(__on_hide, vid, actual_key)
        )
        POPUPS[vid] = actual_key

    def __hide_popup(vid, key, sink):
        if POPUPS.get(vid) == key:
            POPUPS.pop(vid, None)
            sink()

    return partial(__hide_popup, vid, actual_key, inner_hide_popup)


def style_message(message, style):
    # type: (str, Dict[str, str]) -> str
    return """
        <div
            style="padding: 1rem;
                   background-color: {background};
                   color: {foreground}"
        >{message}</div>
    """.format(message=message, **style)


def escape_text(text):
    # type: (str) -> str
    return html.escape(text, quote=False).replace(" ", "&nbsp;")


def focus_view(view):
    # type: (sublime.View) -> None
    window = view.window()
    if not window:
        return

    group, _ = window.get_view_index(view)
    window.focus_group(group)
    window.focus_view(view)


if int(sublime.version()) < 4000:
    from Default import history_list  # type: ignore[import]

    def add_selection_to_jump_history(view):
        history_list.get_jump_history_for_view(view).push_selection(view)

else:
    def add_selection_to_jump_history(view):
        view.run_command("add_jump_record", {
            "selection": [(r.a, r.b) for r in view.sel()]
        })


def line_indentation(line):
    # type: (str) -> int
    return len(line) - len(line.lstrip())


def kill_proc(proc):
    if sys.platform == "win32":
        # terminate would not kill process opened by the shell cmd.exe,
        # it will only kill cmd.exe leaving the child running
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        subprocess.Popen(
            "taskkill /PID %d /T /F" % proc.pid,
            startupinfo=startupinfo)
    else:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.terminate()


class Cache(OrderedDict):
    def __init__(self, maxsize=128):
        assert maxsize > 0
        self.maxsize = maxsize
        super().__init__()

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            self.popitem(last=False)
