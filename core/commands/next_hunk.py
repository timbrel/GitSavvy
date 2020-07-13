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
    from typing import Iterable, Iterator, List, TypeVar
    T = TypeVar("T")

    Point = int
    Row = int


LINE_DISTANCE_BETWEEN_EDITS = 2


class gs_next_hunk(sublime_plugin.TextCommand):
    def is_enabled(self):
        return len(self.view.sel()) > 0

    def run(self, edit):
        if not jump_to_hunk(self.view, "next_modification"):
            flash(self.view, "No hunk to jump to")


class gs_prev_hunk(sublime_plugin.TextCommand):
    def is_enabled(self):
        return len(self.view.sel()) > 0

    def run(self, edit):
        if not jump_to_hunk(self.view, "prev_modification"):
            flash(self.view, "No hunk to jump to")
        else:
            jump_to_hunk(self.view, "prev_modification")
            jump_to_hunk(self.view, "next_modification")


def jump_to_hunk(view, using):
    # type: (sublime.View, str) -> bool
    frozen_sel = [s for s in view.sel()]
    jump_positions = chain([cur_pos(view)], jump(view, using))
    for a, b in pairwise(take_while_unique(jump_positions)):
        if line_distance(view, a, b) >= LINE_DISTANCE_BETWEEN_EDITS:
            line = view.line(b)
            set_sel(view, [sublime.Region(line.a)])
            return True
    else:
        set_sel(view, frozen_sel)
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
    view.show(sel)


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
