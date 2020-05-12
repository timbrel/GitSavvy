from contextlib import contextmanager
import os
import signal
import subprocess
import sys
import time
import threading
import traceback


MYPY = False
if MYPY:
    from typing import Callable, Iterator, Type
    import sublime


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
    window = view.window()
    if window:
        window.status_message(message)


def focus_view(view):
    # type: (sublime.View) -> None
    window = view.window()
    if not window:
        return

    group, _ = window.get_view_index(view)
    window.focus_group(group)
    window.focus_view(view)


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
