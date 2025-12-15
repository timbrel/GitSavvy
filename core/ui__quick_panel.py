from __future__ import annotations

from functools import lru_cache
from typing import Callable, Iterable, List, NamedTuple, Sequence, Tuple, Union

import sublime


__all__ = (
    "Action",
    "ActionType",
    "QuickPanelItems",
    "SEPARATOR",
    "noop",
    "show_actions_panel",
    "show_noop_panel",
    "show_quick_panel",
)


class Action(NamedTuple):
    description: str
    action: Callable[[], None]


ActionType = Tuple[str, Callable[[], None]]
QuickPanelItems = Iterable[Union[str, List[str], sublime.QuickPanelItem]]


def show_quick_panel(
    window: sublime.Window,
    items: QuickPanelItems,
    on_done: Callable[[int], None],
    on_cancel: Callable[[], None] = lambda: None,
    on_highlight: Callable[[int], None] = lambda _: None,
    selected_index: int = -1,
    flags: int = sublime.MONOSPACE_FONT,
) -> None:
    def _on_done(idx: int) -> None:
        if idx == -1:
            on_cancel()
        else:
            on_done(idx)

    # `on_highlight` also gets called `on_done`. We
    # reduce the side-effects here using `lru_cache`.
    @lru_cache(1)
    def _on_highlight(idx: int) -> None:
        on_highlight(idx)

    window.show_quick_panel(
        list(items),
        _on_done,
        on_highlight=_on_highlight,
        selected_index=selected_index,
        flags=flags
    )


def show_actions_panel(
    window: sublime.Window,
    actions: Sequence[ActionType],
    select: int = -1
) -> None:
    def on_selection(idx: int) -> None:
        description, action = actions[idx]
        action()

    show_quick_panel(
        window,
        (action[0] for action in actions),
        on_selection,
        selected_index=select
    )


def show_noop_panel(window: sublime.Window, message: str) -> None:
    show_actions_panel(window, [noop(message)])


def noop(description: str) -> ActionType:
    return Action(description, lambda: None)


SEPARATOR = noop("_" * 74)
