from contextlib import contextmanager
from itertools import chain

import sublime
import sublime_plugin


from GitSavvy.core.fns import pairwise
from GitSavvy.core.utils import flash


__all__ = (
    "gs_next_hunk",
    "gs_prev_hunk",
)


MYPY = False
if MYPY:
    from typing import Callable, Iterable, Iterator, List, TypeVar
    T = TypeVar("T")

    Point = int
    Row = int


LINE_DISTANCE_BETWEEN_EDITS = 2


class gs_next_hunk(sublime_plugin.TextCommand):
    def is_enabled(self):
        return len(self.view.sel()) > 0

    def run(self, edit):
        view = self.view
        if not jump_to_hunk(view, "next_modification"):
            flash(view, "No hunk to jump to")


class gs_prev_hunk(sublime_plugin.TextCommand):
    def is_enabled(self):
        return len(self.view.sel()) > 0

    def run(self, edit):
        view = self.view
        if not jump_to_hunk(view, "prev_modification"):
            flash(view, "No hunk to jump to")
        else:
            with dont_move_viewport(view):
                jump_to_hunk(view, "prev_modification")
            jump_to_hunk(view, "next_modification")


def jump_to_hunk(view, using):
    # type: (sublime.View, str) -> bool
    restore = capture_sel_and_viewport(view)
    jump_positions = chain([cur_pos(view)], jump(view, using))
    for a, b in pairwise(take_while_unique(jump_positions)):
        if line_distance(view, a, b) >= LINE_DISTANCE_BETWEEN_EDITS:
            line = view.line(b)
            r = sublime.Region(line.a)
            set_sel(view, [r])
            show_region(view, r)
            return True
    else:
        restore()
        return False


def jump(view, method):
    # type: (sublime.View, str) -> Iterator[sublime.Region]
    while True:
        view.run_command(method)
        yield cur_pos(view)


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
    # type: (sublime.View, Point) -> Row
    return view.rowcol(pt)[0]


def set_sel(view, selection):
    # type: (sublime.View, List[sublime.Region]) -> None
    sel = view.sel()
    sel.clear()
    sel.add_all(selection)


def capture_sel_and_viewport(view):
    # type: (sublime.View) -> Callable[[], None]
    frozen_sel = [s for s in view.sel()]
    vp = view.viewport_position()

    def restore():
        set_sel(view, frozen_sel)
        view.set_viewport_position(vp)

    return restore


def show_region(view, region, context=5):
    # type: (sublime.View, sublime.Region, int) -> None
    row_a, _ = view.rowcol(region.begin())
    row_b, _ = view.rowcol(region.end())
    adjusted_section = sublime.Region(
        # `text_point` is permissive and normalizes negative rows
        view.text_point(row_a - context, 0),
        view.text_point(row_b + context, 0)
    )
    view.show(adjusted_section, False)


def cur_pos(view):
    # type: (sublime.View) -> sublime.Region
    return view.sel()[0]


def take_while_unique(iterable):
    # type: (Iterable[T]) -> Iterator[T]
    seen = []
    for item in iterable:
        if item in seen:
            break
        seen.append(item)
        yield item


@contextmanager
def dont_move_viewport(view):
    # type: (sublime.View) -> Iterator[None]
    vp = view.viewport_position()
    try:
        yield
    finally:
        view.set_viewport_position(vp)
