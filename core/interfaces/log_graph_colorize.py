from __future__ import annotations

from functools import lru_cache


import sublime

from ..caches import Cache
from ..fns import flatten, partition, take
from ..runtime import HopperR, cooperative_thread_hopper
from . import log_graph_walk as walk
from .log_graph_helper import (
    commit_message_from_point, find_fixups_upwards, find_matching_commit,
    is_fixup_or_squash_message, strip_fixup_or_squash_prefix
)


DOT_SCOPE = 'git_savvy.graph.dot'
DOT_ABOVE_SCOPE = 'git_savvy.graph.dot.above'
PATH_SCOPE = 'git_savvy.graph.path_char'
PATH_ABOVE_SCOPE = 'git_savvy.graph.path_char.above'
MATCHING_COMMIT_SCOPE = 'git_savvy.graph.matching_commit'


def colorize_dots(view: sublime.View) -> None:
    dots = walk.find_graph_art_at_cursor(view) or tuple(walk.dots_from_sel(view))
    _colorize_dots(view.id(), dots)


ACTIVE_COMPUTATION = Cache()


@lru_cache(maxsize=1)
# ^- throttle side-effects
def _colorize_dots(vid: sublime.ViewId, dots: tuple[walk.Char, ...]) -> None:
    view = sublime.View(vid)

    view.add_regions(
        'gs_log_graph.dot',
        [ch.region() for ch in dots],
        scope=DOT_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )

    ACTIVE_COMPUTATION[vid] = dots
    __colorize_dots(vid, dots)


@cooperative_thread_hopper
def __colorize_dots(vid: sublime.ViewId, dots: tuple[walk.Char, ...]) -> HopperR:
    timer = yield "ENSURE_UI_THREAD"
    view = sublime.View(vid)

    paths_down: list[list[walk.Char]] = []
    paths_up: list[list[walk.Char]] = []

    uow = []
    for container, direction in ((paths_down, "down"), (paths_up, "up")):
        for dot in dots:
            try:
                chars = walk.follow_path_if_cached(dot, direction)  # type: ignore[arg-type]
            except ValueError:
                values: list[walk.Char] = []
                container.append(values)
                uow.append((walk.follow_path(dot, direction), values))  # type: ignore[arg-type]
            else:
                container.append(chars)

    c = 0
    while uow:
        idx = c % len(uow)
        iterator, values = uow[idx]
        try:
            char = next(iterator)
        except StopIteration:
            uow.pop(idx)
        else:
            values.append(char)
        c += 1

        if timer.exhausted_ui_budget():
            __paint(view, paths_down, paths_up)
            timer = yield "AWAIT_UI_THREAD"
            if ACTIVE_COMPUTATION.get(vid) != dots:
                return

    if ACTIVE_COMPUTATION[vid] == dots:
        ACTIVE_COMPUTATION.pop(vid, None)
        __paint(view, paths_down, paths_up)


def __paint(view: sublime.View, paths_down: list[list[walk.Char]], paths_up: list[list[walk.Char]]) -> None:
    path_down = flatten(filter(
        lambda path: len(path) > 1,  # type: ignore[arg-type]  # https://github.com/python/mypy/issues/9176
        paths_down
    ))
    chars_up = flatten(filter(
        lambda path: len(path) > 1,  # type: ignore[arg-type]  # https://github.com/python/mypy/issues/9176
        paths_up
    ))
    path_up, dot_up = partition(lambda ch: ch.char() in walk.COMMIT_NODE_CHARS, chars_up)
    view.add_regions(
        'gs_log_graph.path_below',
        [ch.region() for ch in path_down],
        scope=PATH_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )
    view.add_regions(
        'gs_log_graph.path_above',
        [ch.region() for ch in path_up],
        scope=PATH_ABOVE_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )
    view.add_regions(
        'gs_log_graph.dot.above',
        [ch.region() for ch in dot_up],
        scope=DOT_ABOVE_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )


def colorize_fixups(view: sublime.View) -> None:
    dots = tuple(walk.dots_from_sel(view))
    _colorize_fixups(view.id(), dots)


@lru_cache(maxsize=1)
def _colorize_fixups(vid: sublime.ViewId, dots: tuple[walk.Char, ...]) -> None:
    view = sublime.View(vid)

    matching_dots = flatten(__find_matching_dots(vid, dot) for dot in dots)
    view.add_regions(
        'gs_log_graph_follow_fixups',
        [dot.region() for dot in matching_dots],
        scope=MATCHING_COMMIT_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )


@lru_cache(maxsize=64)  # If we cache, we must return non-lazy `List`!
def __find_matching_dots(vid: sublime.ViewId, dot: walk.Char) -> list[walk.Char]:
    view = sublime.View(vid)
    commit_message = commit_message_from_point(view, dot.pt)
    if not commit_message:
        return []

    if is_fixup_or_squash_message(commit_message):
        original_message = strip_fixup_or_squash_prefix(commit_message)
        return take(1, find_matching_commit(dot, original_message))
    else:
        return list(dot for dot, _ in find_fixups_upwards(dot, commit_message))
