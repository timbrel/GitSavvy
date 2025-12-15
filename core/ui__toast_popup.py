from __future__ import annotations

import html
from functools import partial
from itertools import count
from typing import Callable, Dict, Tuple

import sublime


__all__ = ("show_toast", "show_popup")


IDS = partial(next, count())  # type: Callable[[], int]
HIDE_POPUP_TIMERS: Dict[sublime.ViewId, int] = {}
POPUPS: Dict[sublime.ViewId, Tuple[int, int]] = {}
DEFAULT_TIMEOUT = 2500  # [ms]
DEFAULT_STYLE = {
    "background": "transparent",
    "foreground": "var(--foreground)"
}


def show_toast(
    view: sublime.View,
    message: str,
    timeout: int = DEFAULT_TIMEOUT,
    style: Dict[str, str] = DEFAULT_STYLE,
    max_width: float = 2 / 3,
    location: float = 1.0,
) -> Callable[[], None]:
    """Show a toast popup by default at the bottom of the view."""
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

    def on_hide(vid: sublime.ViewId, _key: int) -> None:
        HIDE_POPUP_TIMERS.pop(vid)

    def __hide_popup(vid: sublime.ViewId, key: int, sink: Callable[[], None]) -> None:
        if HIDE_POPUP_TIMERS.get(vid) == key:
            HIDE_POPUP_TIMERS.pop(vid)
            sink()

    inner_hide_popup = show_popup(
        view,
        content,
        max_width=float(max_width),
        location=int(location),
        on_hide=partial(on_hide, vid, key)
    )
    HIDE_POPUP_TIMERS[vid] = key

    hide_popup = partial(__hide_popup, vid, key, inner_hide_popup)
    if timeout > 0:
        sublime.set_timeout(hide_popup, timeout)
    return hide_popup


def show_popup(
    view: sublime.View,
    content: str,
    max_width: float,
    location: int,
    on_hide: Callable[[], None] | None = None
) -> Callable[[], None]:
    vid = view.id()
    inner_hide_popup = view.hide_popup
    actual_key = (int(max_width), location)
    if POPUPS.get(vid) == actual_key:
        view.update_popup(content)
    else:
        def __on_hide(vid: sublime.ViewId, key: Tuple[int, int]) -> None:
            POPUPS.pop(vid)
            if on_hide:
                on_hide()

        view.show_popup(
            content,
            max_width=max_width,
            location=location,
            on_hide=partial(__on_hide, vid, actual_key)
        )
        POPUPS[vid] = actual_key

    def __hide_popup(vid: sublime.ViewId, key: Tuple[int, int], sink: Callable[[], None]) -> None:
        if POPUPS.get(vid) == key:
            POPUPS.pop(vid)
            sink()

    return partial(__hide_popup, vid, actual_key, inner_hide_popup)


def style_message(message: str, style: Dict[str, str]) -> str:
    return """
        <div
            style="padding: 1rem;
                   background-color: {background};
                   color: {foreground}"
        >{message}</div>
    """.format(message=message, **style)


def escape_text(text: str) -> str:
    return html.escape(text, quote=False).replace(" ", "&nbsp;")
