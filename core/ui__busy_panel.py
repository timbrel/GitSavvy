from __future__ import annotations

from itertools import chain, cycle, repeat
from typing import Any, Callable, Iterator, NamedTuple, TypeVar, Union, overload

import sublime

from typing_extensions import TypeAlias

from . import runtime, fns
from .ui__quick_panel import show_panel


__all__ = (
    "AnimatedText",
    "AnimatedText_",
    "show_busy_panel",
)


class AnimatedText_(NamedTuple):
    text: Iterator[str]
    cycle_times: Iterator[int]

    def __next__(self) -> tuple[str, int]:
        return next(self.text), next(self.cycle_times)


def AnimatedText(*text: str, tick: float = 0.2, start_after: float = 0.8):
    ticks = chain(
        [int(start_after * 1000)],
        repeat(int(tick * 1000))
    )
    return AnimatedText_(cycle(text), ticks)


busy_text: TypeAlias = Union[str, AnimatedText_]
T = TypeVar("T")


@overload
def show_busy_panel(  # noqa: E704
    window: sublime.Window, text: busy_text, task: Callable[[], None], kont: Callable[[], None]
) -> None: ...
@overload             # noqa: E302
def show_busy_panel(  # noqa: E704
    window: sublime.Window, text: busy_text, task: Callable[[], None], kont: Callable[[Any], None]
) -> None: ...
@overload             # noqa: E302
def show_busy_panel(  # noqa: E704
    window: sublime.Window, text: busy_text, task: Callable[[], T], kont: Callable[[T], None]
) -> None: ...


def show_busy_panel(
    window: sublime.Window, text: busy_text, task: Callable[[], T], kont: Callable[..., None]
) -> None:
    """
    Displays a busy panel in the Sublime Text window while a task is being executed.

    Note that `task()` runs on Sublime's worker thread, while `kont()` runs on the UI
    thread. A user can abort to run the continuation by pressing the escape key.
    The `task()` itself cannot be aborted.

    The continuation function `kont` can take zero or exactly one argument. In the latter
    case, it will receive the return value of `task()`. Otherwise, that return value will
    be ignored and thrown away.

    Args:
        window: The Sublime Text window where the busy panel will be shown.
        text: The text to display in the busy panel.  It can be a static string
            or an `AnimatedText` instance.
        task: The task to be executed while the busy panel is displayed.
        kont: The continuation function to be called after the task is completed.
    """
    aborted = False
    ignore_next_abort = False

    def abort():
        nonlocal ignore_next_abort, aborted
        if ignore_next_abort:
            ignore_next_abort = False
            return
        aborted = True

    if isinstance(text, str):
        def working_indicator(*_) -> None:
            show_panel(window, [text], working_indicator, abort)
    else:
        def working_indicator(*_) -> None:
            text_, cycle_time = next(text)
            show_panel(window, [text_], working_indicator, abort)
            sublime.set_timeout(tick, cycle_time)

        def tick():
            nonlocal aborted, ignore_next_abort
            if aborted:
                return
            ignore_next_abort = True
            window.run_command("hide_overlay")
            working_indicator()

    @runtime.cooperative_thread_hopper
    def worker():
        nonlocal aborted
        if runtime.it_runs_on_ui():
            yield "AWAIT_WORKER"
        rv = task()
        yield "AWAIT_UI_THREAD"
        if aborted:
            return
        window.run_command("hide_overlay")
        if fns.arity(kont) == 1:
            kont(rv)
        else:
            kont()

    runtime.ensure_on_ui(working_indicator)
    worker()
