from __future__ import annotations
from contextlib import contextmanager
import datetime
from itertools import count
import os
import signal
import subprocess
import sys
import time
import threading
import traceback
from types import SimpleNamespace

import sublime

from . import runtime


from typing import Callable, Iterator, Optional, Sequence, Type


@contextmanager
def print_runtime(message):
    # type: (str) -> Iterator[None]
    start_time = time.perf_counter()
    yield
    end_time = time.perf_counter()
    duration = round((end_time - start_time) * 1000)
    thread_name = threading.current_thread().name[0]
    print('{} took {}ms [{}]'.format(message, duration, thread_name))


@contextmanager
def measure_runtime():
    start_time = time.perf_counter()
    ms = SimpleNamespace()
    yield ms
    end_time = time.perf_counter()
    duration = round((end_time - start_time) * 1000)
    ms.get = lambda: duration


def print_runtime_marks():
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


def is_younger_than(timedelta: datetime.timedelta, now: datetime.datetime, timestamp: int) -> bool:
    dt = datetime.datetime.utcfromtimestamp(timestamp)
    return (now - dt) < timedelta


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


def uprint(msg):
    # type: (str) -> None
    """Print help message for undoing actions"""
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


HIGHLIGHT_REGION_KEY = "GS.flashs.{}"
DURATION = 0.4
STYLE = {"scope": "git_savvy.graph.dot", "flags": sublime.RegionFlags.NO_UNDO}


def flash_regions(view, regions, key="default"):
    # type: (sublime.View, Sequence[sublime.Region], str) -> None
    region_key = HIGHLIGHT_REGION_KEY.format(key)
    view.add_regions(region_key, regions, **STYLE)  # type: ignore[arg-type]

    sublime.set_timeout(
        runtime.throttled(erase_regions, view, region_key),
        int(DURATION * 1000)
    )


def erase_regions(view, region_key):
    # type: (sublime.View, str) -> None
    view.erase_regions(region_key)


def yes_no_switch(name, value):
    # type: (str, Optional[bool]) -> Optional[str]
    assert name.startswith("--")
    if value is None:
        return None
    if value:
        return name
    return "--no-{}".format(name[2:])


def focus_view(view):
    # type: (sublime.View) -> None
    window = view.window()
    if not window:
        return

    group, _ = window.get_view_index(view)
    window.focus_group(group)
    window.focus_view(view)


if int(sublime.version()) < 4000:
    from Default import history_list  # type: ignore

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


STARTUPINFO = None
if sys.platform == "win32":
    STARTUPINFO = subprocess.STARTUPINFO()
    STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW


def open_folder_in_new_window(path: str, *, then: Callable[[sublime.Window], None] = None):
    bin = get_sublime_executable()
    cmd = [bin, path]
    subprocess.Popen(cmd, startupinfo=STARTUPINFO)

    if then:
        @runtime.on_worker
        def search_for_new_window(_tries=5):
            for w in sublime.windows():
                if path in w.folders():
                    then(w)
                    return
            sublime.set_timeout(lambda: search_for_new_window(_tries - 1), 10)

        search_for_new_window()


def get_sublime_executable() -> str:
    executable_path = sublime.executable_path()
    if sublime.platform() == "osx":
        app_path = executable_path[: executable_path.rfind(".app/") + 5]
        executable_path = app_path + "Contents/SharedSupport/bin/subl"

    return executable_path


def kill_proc(proc: subprocess.Popen) -> None:
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


def try_kill_proc(proc: Optional[subprocess.Popen]) -> None:
    if proc:
        try:
            kill_proc(proc)
        except ProcessLookupError:
            pass
        proc.got_killed = True  # type: ignore[attr-defined]


def proc_has_been_killed(proc: subprocess.Popen) -> bool:
    return getattr(proc, "got_killed", False)


# `realpath` also supports `bytes` and we don't, hence the indirection
def _resolve_path(path):
    # type: (str) -> str
    return os.path.realpath(path)


if (
    sys.platform == "win32"
    and sys.version_info < (3, 8)
    and sys.getwindowsversion()[:2] >= (6, 0)
):
    try:
        from nt import _getfinalpathname
    except ImportError:
        resolve_path = _resolve_path
    else:
        def resolve_path(path):
            # type: (str) -> str
            rpath = _getfinalpathname(path)
            if rpath.startswith("\\\\?\\"):
                rpath = rpath[4:]
                if rpath.startswith("UNC\\"):
                    rpath = "\\" + rpath[3:]
            return rpath

else:
    resolve_path = _resolve_path


def paths_upwards(path):
    # type: (str) -> Iterator[str]
    while True:
        yield path
        path, name = os.path.split(path)
        if not name or path == "/":
            break


class Counter:
    """Thread-safe, lockless counter.

    Implementation idea from @grantjenks
    https://github.com/jd/fastcounter/issues/2#issue-548504668
    """
    def __init__(self):
        self._incs = count()
        self._decs = count()

    def inc(self):
        next(self._incs)

    def dec(self):
        next(self._decs)

    def count(self) -> int:
        return next(self._incs) - next(self._decs)
