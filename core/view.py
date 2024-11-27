from contextlib import contextmanager, ExitStack
from functools import lru_cache

import sublime

from .runtime import text_command


from typing import Callable, ContextManager, Iterator, List, NamedTuple, Optional, TypeVar
from .types import Row, Col
WrapperFn = Callable[[sublime.View], ContextManager[None]]
T_float = TypeVar("T_float", int, float)


class Position(NamedTuple):
    row: Row
    col: Col
    offset: Optional[float]


def find_by_selector(view, selector):
    # type: (sublime.View, str) -> List[sublime.Region]
    # Same as `view.find_by_selector` but cached.
    return _find_by_selector(view.id(), view.change_count(), selector)


@lru_cache(maxsize=16)
def _find_by_selector(vid, _cc, selector):
    # type: (sublime.ViewId, int, str) -> List[sublime.Region]
    view = sublime.View(vid)
    return view.find_by_selector(selector)


def show_region(view, region, context=5, prefer_end=False):
    # type: (sublime.View, sublime.Region, int, bool) -> None
    # Differentiate between wide and short jumps. For
    # short jumps minimize the scrolling.
    if touching_regions(view.visible_region(), region):
        row_a, _ = view.rowcol(region.begin())
        row_b, _ = view.rowcol(region.end())
        adjusted_section = sublime.Region(
            # `text_point` is permissive and normalizes negative rows
            view.text_point(row_a - context, 0),
            view.text_point(row_b + context, 0)
        )
        view.show(
            adjusted_section if prefer_end else flip_region(adjusted_section),
            False
        )

    else:
        # For long jumps, usually keep the target, for example `region.begin()`,
        # around center (`vh / 2`).  But try to always show the whole region
        # if it fits.
        lh = view.line_height()
        _, vh = view.viewport_extent()
        _, rt = view.text_to_layout(region.begin())
        _, rb = view.text_to_layout(region.end())
        rh = abs(rb - rt) + lh
        ch = context * lh
        offset = clamp(
            clamp(lh, ch, vh - rh),
            vh / 2,
            vh - (rh + ch)
        )
        new_top = max(0, (rb if prefer_end else rt) - offset)
        view.set_viewport_position((0.0, new_top))


def touching_regions(a, b):
    # type: (sublime.Region, sublime.Region) -> bool
    return a.intersects(b) or a.contains(b)


def join_regions(a, b):
    # type: (sublime.Region, sublime.Region) -> sublime.Region
    return sublime.Region(
        min(a.begin(), b.begin()),
        max(a.end(), b.end())
    )


def flip_region(r):
    # type: (sublime.Region) -> sublime.Region
    return sublime.Region(r.b, r.a)


def clamp(lo, hi, v):
    # type: (T_float, T_float, T_float) -> T_float
    return max(lo, min(hi, v))


def line_distance(view, a, b):
    # type: (sublime.View, sublime.Region, sublime.Region) -> int
    a, b = sorted((a, b), key=lambda region: region.begin())

    # If a region `a` already contains a trailing "\n" just using
    # `view.line(a)` will not strip this newline character but
    # `split_by_newlines` does.
    # E.g. for a region `(1136, 1253)` `split_by_newlines` last region
    # is                `(1214, 1252)`
    #                              ^
    a_end = view.split_by_newlines(a)[-1].end()
    b_start = b.begin()
    return abs(row_on_pt(view, a_end) - row_on_pt(view, b_start))


def row_on_pt(view, pt):
    # type: (sublime.View, sublime.Point) -> Row
    return view.rowcol(pt)[0]


def capture_cur_position(view):
    # type: (sublime.View) -> Optional[Position]
    try:
        sel = view.sel()[0]
    except Exception:
        return None

    cursor = sel.b
    row, col = view.rowcol(cursor)
    return Position(row, col, y_offset(view, cursor))


def y_offset(view, cursor):
    # type: (sublime.View, int) -> float
    _, cy = view.text_to_layout(cursor)
    _, vy = view.viewport_position()
    return cy - vy


def scroll_to_pt(view, pt, offset, no_overscroll=False):
    # type: (sublime.View, int, float, bool) -> None
    if no_overscroll:
        _, vh = view.viewport_extent()
        if not (0 < offset < vh):
            view.show(pt)
            return

    _, cy = view.text_to_layout(pt)
    vy = cy - offset
    vx, _ = view.viewport_position()
    view.set_viewport_position((vx, vy), animate=False)


def place_cursor_and_show(view, pt, row_offset, no_overscroll=False):
    # type: (sublime.View, int, Optional[float], bool) -> None
    view.sel().clear()
    view.sel().add(pt)
    if row_offset is None:
        view.show(pt)
    else:
        scroll_to_pt(view, pt, row_offset, no_overscroll)


def apply_position(view, row, col, row_offset, no_overscroll=False):
    # type: (sublime.View, Row, Col, Optional[float], bool) -> None
    pt = view.text_point(row, col)
    place_cursor_and_show(view, pt, row_offset, no_overscroll)


def place_view(window, view, after):
    # type: (sublime.Window, sublime.View, sublime.View) -> None
    view_group, current_index = window.get_view_index(view)
    group, index = window.get_view_index(after)
    if view_group == group:
        wanted_index = index + 1 if index < current_index else index
        window.set_view_index(view, group, wanted_index)


def other_visible_views(view: sublime.View) -> Iterator[sublime.View]:
    """Yield all visible views of the active window except the given view itself."""
    window = view.window()
    if not window:
        return

    for view_ in visible_views(window):
        if view_ != view:
            yield view_


def visible_views(window: sublime.Window = None) -> Iterator[sublime.View]:
    yield from (
        sheets_view
        for window_ in ([window] if window else sublime.windows())
        for group_id in range(window_.num_groups())
        for sheet in window_.selected_sheets_in_group(group_id)
        if (sheets_view := sheet.view())
    )


# `replace_view_content` is a wrapper for `_replace_region` to get some
# typing support from mypy.
def replace_view_content(view, text, region=None, wrappers=[]):
    # type: (sublime.View, str, sublime.Region, List[WrapperFn]) -> None
    """Replace the content of the view

    If no region is given the whole content will get replaced. Otherwise
    only the selected region.
    """
    _replace_region(view, text, region, wrappers)


@text_command
def _replace_region(view, edit, text, region=None, wrappers=[]):
    # type: (sublime.View, sublime.Edit, str, sublime.Region, List[WrapperFn]) -> None
    if region is None:
        # If you "replace" (or expand) directly at the cursor,
        # the cursor expands into a selection.
        # This is a common case for an empty view so we take
        # care of it out of box.
        region = sublime.Region(0, max(1, view.size()))

    wrappers = wrappers[:] + [stable_viewport]
    if any(
        region.contains(s) or region.intersects(s)
        for s in view.sel()
    ):
        wrappers += [restore_cursors]

    with ExitStack() as stack:
        for wrapper in wrappers:
            stack.enter_context(wrapper(view))
        stack.enter_context(writable_view(view))
        view.replace(edit, region, text)


@contextmanager
def writable_view(view):
    # type: (sublime.View) -> Iterator[None]
    is_read_only = view.is_read_only()
    view.set_read_only(False)
    try:
        yield
    finally:
        view.set_read_only(is_read_only)


@contextmanager
def restore_cursors(view):
    # type: (sublime.View) -> Iterator[None]
    save_cursors = [
        (view.rowcol(s.begin()), view.rowcol(s.end()))
        for s in view.sel()
    ] or [((0, 0), (0, 0))]

    try:
        yield
    finally:
        view.sel().clear()
        for (begin, end) in save_cursors:
            view.sel().add(
                sublime.Region(view.text_point(*begin), view.text_point(*end))
            )


@contextmanager
def stable_viewport(view):
    # type: (sublime.View) -> Iterator[None]
    # Ref: https://github.com/SublimeTextIssues/Core/issues/2560
    # See https://github.com/jonlabelle/SublimeJsPrettier/pull/171/files
    # for workaround.
    vx, vy = view.viewport_position()
    try:
        yield
    finally:
        view.set_viewport_position((0, 0), animate=False)  # intentional!
        view.set_viewport_position((vx, vy), animate=False)
