from functools import lru_cache, partial, wraps
from collections import OrderedDict
from contextlib import contextmanager
import datetime
import html
import inspect
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


from typing import (
    Any, Callable, Dict, Iterable, Iterator, NamedTuple, Optional, Sequence,
    Tuple, Type, TypeVar, Union, TYPE_CHECKING)

if TYPE_CHECKING:
    from typing_extensions import ParamSpec
    P = ParamSpec('P')
    T = TypeVar('T')


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


IDS = partial(next, count())  # type: Callable[[], int]
HIDE_POPUP_TIMERS = {}  # type: Dict[sublime.ViewId, int]
POPUPS = {}  # type: Dict[sublime.ViewId, Tuple]
DEFAULT_TIMEOUT = 2500  # [ms]
DEFAULT_STYLE = {
    'background': 'transparent',
    'foreground': 'var(--foreground)'
}


def show_toast(
    view,
    message,
    timeout=DEFAULT_TIMEOUT,
    style=DEFAULT_STYLE,
    max_width=2 / 3,
    location=1.0,
):
    # type: (sublime.View, str, int, Dict[str, str], float, float) -> Callable[[], None]
    """Show a toast popup by default at the bottom of the view.

    A `timeout` of -1 makes a "sticky" toast.
    `max_width` and `location` can take floats between 0 and zero.
    For `location` 0.0 means top of the viewport, 1.0 bottom; e.g. 0.5 is
    the middle of the screen.
    Same for `max_width`, 1.0 denoting the whole viewport.
    """
    messages_by_line = escape_text(message).splitlines()
    content = style_message("<br />".join(messages_by_line), style)

    # Order can matter here.  If we calc width *after* visible_region we get
    # different results!
    if isinstance(max_width, float) and 0 <= max_width <= 1:
        width, _ = view.viewport_extent()
        max_width = width * max_width

    if isinstance(max_width, float) and 0 <= location <= 1:
        visible_region = view.visible_region()
        r0, _ = view.rowcol(visible_region.a)
        r1, _ = view.rowcol(visible_region.b)
        r_ = r0 + int(((r1 - r0) * location)) - 4 - len(messages_by_line)
        location = view.text_point(max(r0, r_), 0)

    vid = view.id()
    key = IDS()

    def on_hide(vid, key):
        HIDE_POPUP_TIMERS.pop(vid, None)

    def __hide_popup(vid, key, sink):
        if HIDE_POPUP_TIMERS.get(vid) == key:
            HIDE_POPUP_TIMERS.pop(vid, None)
            sink()

    inner_hide_popup = show_popup(
        view,
        content,
        max_width=max_width,
        location=int(location),
        on_hide=partial(on_hide, vid, key)
    )
    HIDE_POPUP_TIMERS[vid] = key

    hide_popup = partial(__hide_popup, vid, key, inner_hide_popup)
    if timeout > 0:
        sublime.set_timeout(hide_popup, timeout)
    return hide_popup


def show_popup(view, content, max_width, location, on_hide=None):
    # type: (sublime.View, str, float, int, Callable[[], None]) -> Callable[[], None]
    vid = view.id()
    inner_hide_popup = view.hide_popup
    actual_key = (int(max_width), location)
    if POPUPS.get(vid) == actual_key:
        view.update_popup(content)
    else:
        def __on_hide(vid, key):
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


class Action(NamedTuple):
    description: str
    action: Callable[[], None]


ActionType = Tuple[str, Callable[[], None]]
QuickPanelItems = Iterable[Union[str, sublime.QuickPanelItem]]


def show_panel(
    window,  # type: sublime.Window
    items,  # type: QuickPanelItems
    on_done,  # type: Callable[[int], None]
    on_cancel=lambda: None,  # type: Callable[[], None]
    on_highlight=lambda _: None,  # type: Callable[[int], None]
    selected_index=-1,  # type: int
    flags=sublime.MONOSPACE_FONT
):
    # (...) -> None
    def _on_done(idx):
        # type: (int) -> None
        if idx == -1:
            on_cancel()
        else:
            on_done(idx)

    # `on_highlight` also gets called `on_done`. We
    # reduce the side-effects here using `lru_cache`.
    @lru_cache(1)
    def _on_highlight(idx):
        # type: (int) -> None
        on_highlight(idx)

    window.show_quick_panel(
        list(items),
        _on_done,
        on_highlight=_on_highlight,
        selected_index=selected_index,
        flags=flags
    )


def show_actions_panel(window, actions, select=-1):
    # type: (sublime.Window, Sequence[ActionType], int) -> None
    def on_selection(idx):
        # type: (int) -> None
        description, action = actions[idx]
        action()

    show_panel(
        window,
        (action[0] for action in actions),
        on_selection,
        selected_index=select
    )


def show_noop_panel(window, message):
    # type: (sublime.Window, str) -> None
    show_actions_panel(window, [noop(message)])


def noop(description):
    # type: (str) -> ActionType
    return Action(description, lambda: None)


SEPARATOR = noop("_" * 74)


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


class Cache(OrderedDict):
    def __init__(self, maxsize=128):
        assert maxsize > 0
        self.maxsize = maxsize
        super().__init__()

    def __getitem__(self, key):
        value = super().__getitem__(key)
        # py>3.8 is optimized such that `pop` and `popitem`
        # call `__getitem__` but `move_to_end` already throws.
        try:
            self.move_to_end(key)
        except KeyError:
            pass
        return value

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            self.popitem(last=False)


general_purpose_cache = Cache(maxsize=512)  # type: Dict[Tuple, Any]


def cached(not_if, cache=general_purpose_cache):
    # type: (Dict[str, Callable], Dict[Tuple, Any]) -> Callable[[Callable[P, T]], Callable[P, T]]
    def decorator(fn):
        # type: (Callable[P, T]) -> Callable[P, T]
        fn_s = inspect.signature(fn)

        def should_skip(arguments):
            return any(
                fn(arguments[name])
                for name, fn in not_if.items()
            )

        @wraps(fn)
        def decorated(*args, **kwargs):
            # type: (P.args, P.kwargs) -> T
            arguments = _bind_arguments(fn_s, args, kwargs)
            if should_skip(arguments):
                return fn(*args, **kwargs)

            key = (fn.__name__,) + tuple(sorted(arguments.items()))
            try:
                return cache[key]
            except KeyError:
                rv = cache[key] = fn(*args, **kwargs)
                return rv

        return decorated
    return decorator


def _bind_arguments(sig, args, kwargs):
    bound = sig.bind(*args, **kwargs)
    arguments = bound.arguments

    def default_value_of(parameter):
        if parameter.default is not parameter.empty:
            return parameter.default
        if parameter.kind is parameter.VAR_KEYWORD:
            return {}
        if parameter.kind is parameter.VAR_POSITIONAL:
            return tuple()

    return {
        name: (arguments[name] if name in arguments else default_value_of(p))
        for name, p in sig.parameters.items()
        if name != "self"
    }


def cache_in_store_as(key):
    # type: (str) -> Callable[[Callable[P, T]], Callable[P, T]]
    """Store the return value of the decorated function in the store."""
    def decorator(fn):
        # type: (Callable[P, T]) -> Callable[P, T]
        @wraps(fn)
        def decorated(*args, **kwargs):
            # type: (P.args, P.kwargs) -> T
            rv = fn(*args, **kwargs)
            self = args[0]
            self.update_store({key: rv})  # type: ignore[attr-defined]
            return rv

        return decorated
    return decorator


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
