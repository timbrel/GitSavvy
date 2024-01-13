from contextlib import contextmanager
from functools import partial
from itertools import chain, takewhile

import sublime
import sublime_plugin


from GitSavvy.core.fns import pairwise
from GitSavvy.core.utils import flash
from GitSavvy.core.view import line_distance, show_region, touching_regions


__all__ = (
    "gs_next_hunk",
    "gs_prev_hunk",
)


from typing import Iterable, Iterator, List, Optional, TypeVar
T = TypeVar("T")


LINE_DISTANCE_BETWEEN_EDITS = 2


class gs_next_hunk(sublime_plugin.TextCommand):
    def is_enabled(self):
        return len(self.view.sel()) > 0

    def run(self, edit):
        view = self.view
        if not jump_to_hunk(view, True):
            flash(view, "No hunk to jump to")


class gs_prev_hunk(sublime_plugin.TextCommand):
    def is_enabled(self):
        return len(self.view.sel()) > 0

    def run(self, edit):
        view = self.view
        if not jump_to_hunk(view, False):
            flash(view, "No hunk to jump to")


def jump_to_hunk(view, forwards):
    # type: (sublime.View, bool) -> bool
    with restore_sel_and_viewport(view):
        hunk = hunk_region(view, forwards)

    if hunk is None:
        is_visible = partial(touching_regions, view.visible_region())
        if not any(filter(is_visible, view.sel())):
            view.show(view.sel())
        return False
    else:
        mark_and_show_line_start(view, hunk)
        return True


def mark_and_show_line_start(view, region):
    # type: (sublime.View, sublime.Region) -> None
    line = view.line(region)
    r = sublime.Region(line.a)
    set_sel(view, [r])
    show_region(view, region)


def hunk_region(view, forwards=True):
    # type: (sublime.View, bool) -> Optional[sublime.Region]
    mods = sorted(
        modifications_per_hunk(view, forwards),
        key=lambda r: r.begin()
    )
    if not mods:
        return None
    a, b = mods[0], mods[-1]
    return sublime.Region(a.begin(), b.end())


def modifications_per_hunk(view, forwards=True):
    # type: (sublime.View, bool) -> Iterator[sublime.Region]
    jump_positions = pairwise(chain(
        [cur_pos(view)], all_modifications(view, forwards)
    ))
    try:
        yield next(
            b for a, b in jump_positions
            if line_distance(view, a, b) >= LINE_DISTANCE_BETWEEN_EDITS
        )
    except StopIteration:
        return

    yield from (
        b for a, b in takewhile(
            lambda a_b: line_distance(view, *a_b) < LINE_DISTANCE_BETWEEN_EDITS,
            jump_positions
        )
    )


def all_modifications(view, forwards=True):
    # type: (sublime.View, bool) -> Iterator[sublime.Region]
    method = "next_modification" if forwards else "prev_modification"
    return take_while_unique(jump(view, method))


def jump(view, method):
    # type: (sublime.View, str) -> Iterator[sublime.Region]
    while True:
        view.run_command(method)
        yield cur_pos(view)


def set_sel(view, selection):
    # type: (sublime.View, List[sublime.Region]) -> None
    sel = view.sel()
    sel.clear()
    sel.add_all(selection)


@contextmanager
def restore_sel_and_viewport(view):
    # type: (sublime.View) -> Iterator[None]
    frozen_sel = [s for s in view.sel()]
    vp = view.viewport_position()
    try:
        yield
    finally:
        set_sel(view, frozen_sel)
        view.set_viewport_position(vp, animate=False)


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
