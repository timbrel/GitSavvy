from __future__ import annotations
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache, partial
from itertools import chain, count, groupby, islice
import os
from queue import Empty
import re
import shlex
import subprocess
import textwrap
import time
import threading
from typing import NamedTuple

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from . import log_graph_colorizer as colorizer, show_commit_info
from .log import gs_log
from .. import utils
from ..base_commands import GsTextCommand
from ..fns import filter_, flatten, pairwise, partition, take, unique
from ..git_command import GitCommand, GitSavvyError
from ..parse_diff import Region, TextRange
from ..settings import GitSavvySettings
from .. import store
from ..runtime import (
    cooperative_thread_hopper,
    enqueue_on_ui,
    enqueue_on_worker,
    run_and_check_timeout,
    run_or_timeout,
    run_on_new_thread,
    text_command,
    time_budget,
    HopperR
)
from ..view import (
    find_by_selector,
    join_regions,
    line_distance,
    replace_view_content,
    show_region,
    visible_views
)
from ..ui_mixins.input_panel import show_single_line_input_panel
from ..ui_mixins.quick_panel import show_branch_panel
from ..utils import add_selection_to_jump_history, flash, focus_view, show_toast, Cache, SEPARATOR
from ...common import util
from ...common.theme_generator import ThemeGenerator


__all__ = (
    "gs_graph",
    "gs_graph_current_file",
    "gs_graph_current_path",
    "gs_graph_pickaxe",
    "gs_log_graph_refresh",
    "gs_log_graph",
    "gs_log_graph_tab_out",
    "gs_log_graph_tab_in",
    "gs_log_graph_current_branch",
    "gs_log_graph_all_branches",
    "gs_log_graph_by_author",
    "gs_log_graph_by_branch",
    "gs_log_graph_navigate",
    "gs_log_graph_navigate_wide",
    "gs_log_graph_navigate_to_head",
    "gs_log_graph_edit_branches",
    "gs_log_graph_edit_filters",
    "gs_input_handler_go_history",
    "gs_log_graph_reset_filters",
    "gs_log_graph_toggle_overview",
    "gs_log_graph_edit_files",
    "gs_log_graph_toggle_all_setting",
    "gs_log_graph_open_commit",
    "gs_log_graph_toggle_commit_info_panel",
    "gs_log_graph_show_and_focus_panel",
    "gs_log_graph_action",
    "GsLogGraphCursorListener",
)


from typing import (
    Callable, Deque, Dict, Generic, Iterable, Iterator, List, Literal, Optional, Set, Sequence, Tuple,
    TypedDict, TypeVar, Union, TYPE_CHECKING
)
from ..git_mixins.branches import Branch
T = TypeVar('T')


QUICK_PANEL_SUPPORTS_WANT_EVENT = int(sublime.version()) >= 4096
DEFAULT_NODE_CHAR = "●"
ROOT_NODE_CHAR = "⌂"
GRAPH_CHAR_OPTIONS = r" /_\|\-\\."
COMMIT_LINE = re.compile(
    r"^[{graph_chars}]*(?P<dot>[{node_chars}])[{graph_chars}]* "
    r"(?P<commit_hash>[a-f0-9]{{5,40}}) +"
    r"(\((?P<decoration>.+?)\))?"
    .format(graph_chars=GRAPH_CHAR_OPTIONS, node_chars=colorizer.COMMIT_NODE_CHARS)
)

DOT_SCOPE = 'git_savvy.graph.dot'
DOT_ABOVE_SCOPE = 'git_savvy.graph.dot.above'
PATH_SCOPE = 'git_savvy.graph.path_char'
PATH_ABOVE_SCOPE = 'git_savvy.graph.path_char.above'
MATCHING_COMMIT_SCOPE = 'git_savvy.graph.matching_commit'
NO_FILTERS = ([], "", "")  # type: Tuple[List[str], str, str]


def compute_identifier_for_view(view):
    # type: (sublime.View) -> Optional[Tuple]
    settings = view.settings()
    if not settings.get('git_savvy.log_graph_view'):
        return None

    apply_filters = settings.get('git_savvy.log_graph_view.apply_filters')
    return (
        settings.get('git_savvy.repo_path'),
        (
            settings.get('git_savvy.log_graph_view.all_branches')
            or settings.get('git_savvy.log_graph_view.branches')
        ),
        (
            (
                settings.get('git_savvy.log_graph_view.paths'),
                settings.get('git_savvy.log_graph_view.filters'),
                settings.get('git_savvy.log_graph_view.filter_by_author')
            ) if apply_filters
            else NO_FILTERS
        )
    )


class gs_graph(WindowCommand, GitCommand):
    def run(
        self,
        repo_path=None,
        file_path=None,
        overview=False,
        all=False,
        show_tags=True,
        branches=None,
        author='',
        title='GRAPH',
        follow=None,
        decoration=None,
        filters='',
    ):
        if repo_path is None:
            repo_path = self.repo_path
        assert repo_path
        paths = (
            [self.get_rel_path(file_path) if os.path.isabs(file_path) else file_path]
            if file_path
            else []
        )
        if branches is None:
            branches = []
        apply_filters = paths or filters or author

        this_id = (
            repo_path,
            all or branches,
            (paths, filters, author) if apply_filters else NO_FILTERS
        )
        for view in self.window.views():
            other_id = compute_identifier_for_view(view)
            standard_graph_views = (
                []
                if branches
                else [(repo_path, True, NO_FILTERS), (repo_path, [], NO_FILTERS)]
            )
            if other_id in [this_id] + standard_graph_views:
                settings = view.settings()
                settings.set("git_savvy.log_graph_view.overview", overview)
                settings.set("git_savvy.log_graph_view.all_branches", all)
                settings.set("git_savvy.log_graph_view.show_tags", show_tags)
                settings.set("git_savvy.log_graph_view.branches", branches)
                if decoration is not None:
                    settings.set('git_savvy.log_graph_view.decoration', decoration)
                settings.set('git_savvy.log_graph_view.apply_filters', apply_filters)
                if apply_filters:
                    settings.set('git_savvy.log_graph_view.paths', paths)
                    settings.set('git_savvy.log_graph_view.filters', filters)
                    settings.set("git_savvy.log_graph_view.filter_by_author", author)
                if follow:
                    settings.set('git_savvy.log_graph_view.follow', follow)

                if follow and follow != extract_symbol_to_follow(view):
                    if show_commit_info.panel_is_visible(self.window):
                        # Hack to force a synchronous update of the panel
                        # *as a result of* `navigate_to_symbol` (by
                        # `on_selection_modified`) since we know that
                        # "show_commit_info" will run blocking if the panel
                        # is empty (or closed).
                        panel = show_commit_info.ensure_panel(self.window)
                        replace_view_content(panel, "")
                    navigate_to_symbol(view, follow)

                if self.window.active_view() != view:
                    focus_view(view)
                else:
                    view.run_command("gs_log_graph_refresh")
                break
        else:
            if follow is None:
                follow = "HEAD"
            if decoration is None:
                decoration = "sparse"
            show_commit_info_panel = bool(self.savvy_settings.get("graph_show_more_commit_info"))
            view = util.view.create_scratch_view(self.window, "log_graph", {
                "title": title,
                "syntax": "Packages/GitSavvy/syntax/graph.sublime-syntax",
                "git_savvy.repo_path": repo_path,
                "git_savvy.log_graph_view.paths": paths,
                "git_savvy.log_graph_view.overview": overview,
                "git_savvy.log_graph_view.all_branches": all,
                "git_savvy.log_graph_view.show_tags": show_tags,
                "git_savvy.log_graph_view.filter_by_author": author,
                "git_savvy.log_graph_view.branches": branches,
                "git_savvy.log_graph_view.follow": follow,
                "git_savvy.log_graph_view.decoration": decoration,
                "git_savvy.log_graph_view.filters": filters,
                "git_savvy.log_graph_view.apply_filters": apply_filters,
                "git_savvy.log_graph_view.show_commit_info_panel": show_commit_info_panel,
            })
            view.run_command("gs_handle_vintageous")
            view.run_command("gs_handle_arrow_keys")
            run_on_new_thread(augment_color_scheme, view)

            # We need to ensure the panel has been created, so it appears
            # e.g. in the menu. Otherwise Sublime will not handle `show_panel`
            # events for that panel at all.
            # Note that the following is basically what `on_activated` does,
            # but `on_activated` runs synchronous when a view gets created t.i.
            # even before we can mark it as "graph_view" in the settings.
            show_commit_info.ensure_panel(self.window)
            if (
                not show_commit_info.panel_is_visible(self.window)
                and show_commit_info_panel
            ):
                self.window.run_command("show_panel", {"panel": "output.show_commit_info"})

            view.run_command("gs_log_graph_refresh")


class gs_graph_current_file(WindowCommand, GitCommand):
    def run(self, **kwargs):
        file_path = self.file_path
        if file_path:
            self.window.run_command("gs_graph", {"file_path": file_path, **kwargs})
        else:
            self.window.status_message("View has no filename to track.")


class gs_graph_current_path(WindowCommand, GitCommand):
    def run(self, **kwargs) -> None:
        if (
            (file_path := self.file_path)
            and (path := os.path.dirname(file_path))
        ):
            self.window.run_command("gs_graph", {"file_path": path, **kwargs})
        else:
            self.window.status_message("View has no path to track.")


class gs_graph_pickaxe(TextCommand, GitCommand):
    def run(self, edit):
        # type: (sublime.Edit) -> None
        view = self.view
        window = view.window()
        if not window:
            return
        repo_path = self.repo_path
        frozen_sel = list(view.sel())
        filters = " ".join(
            shlex.quote("-S{}".format(s))
            for r in frozen_sel
            if (s := view.substr(r))
            if (s.strip())
        )
        if not filters:
            flash(view, "Nothing selected.")
            return

        window.run_command("gs_graph", {"repo_path": repo_path, "filters": filters})


def augment_color_scheme(view):
    # type: (sublime.View) -> None
    settings = GitSavvySettings()
    colors = settings.get('colors').get('log_graph')
    if not colors:
        return

    themeGenerator = ThemeGenerator.for_view(view)
    themeGenerator.add_scoped_style(
        "GitSavvy Highlighted Commit Dot",
        DOT_SCOPE,
        background=colors['commit_dot_background'],
        foreground=colors['commit_dot_foreground'],
    )
    themeGenerator.add_scoped_style(
        "GitSavvy Highlighted Path Char",
        PATH_SCOPE,
        background=colors['path_background'],
        foreground=colors['path_foreground'],
    )
    themeGenerator.add_scoped_style(
        "GitSavvy Highlighted Commit Dot Above",
        DOT_ABOVE_SCOPE,
        background=colors['commit_dot_above_background'],
        foreground=colors['commit_dot_above_foreground'],
    )
    themeGenerator.add_scoped_style(
        "GitSavvy Highlighted Path Char Above",
        PATH_ABOVE_SCOPE,
        background=colors['path_above_background'],
        foreground=colors['path_above_foreground'],
    )
    themeGenerator.add_scoped_style(
        "GitSavvy Highlighted Matching Commit",
        MATCHING_COMMIT_SCOPE,
        background=colors['matching_commit_background'],
        foreground=colors['matching_commit_foreground'],
    )
    themeGenerator.apply_new_theme("log_graph_view", view)


GIT_SUPPORTS_HUMAN_DATE_FORMAT = (2, 21, 0)
FALLBACK_DATE_FORMAT = 'format:%Y-%m-%d %H:%M'


class Ins(NamedTuple):
    idx: int
    line: str


class Del(NamedTuple):
    start: int
    end: int


class Replace(NamedTuple):
    start: int
    end: int
    text: List[str]


MAX_LOOK_AHEAD = 10000
if TYPE_CHECKING:
    from enum import Enum

    class FlushT(Enum):
        token = 0
    Flush = FlushT.token

else:
    Flush = object()


def diff(a, b):
    # type: (Sequence[str], Iterable[str]) -> Iterator[Union[Ins, Del, FlushT]]
    budget_exhausted = time_budget(100)
    a_index = 0
    b_index = -1  # init in case b is empty
    len_a = len(a)
    a_set = set(a)
    for b_index, line in enumerate(b):
        if budget_exhausted():
            yield Flush

        is_commit_line = re.match(FIND_COMMIT_HASH, line)
        if is_commit_line and line not in a_set:
            len_a += 1
            yield Ins(b_index, line)
            continue

        look_ahead = MAX_LOOK_AHEAD if is_commit_line else 1
        try:
            i = a.index(line, a_index, a_index + look_ahead) - a_index
        except ValueError:
            len_a += 1
            yield Ins(b_index, line)
        else:
            if i == 0:
                a_index += 1
                yield Flush
            else:
                len_a -= i
                a_index += i + 1
                yield Del(b_index, b_index + i)

    if b_index < (len_a - 1):
        yield Del(b_index + 1, len_a)


def simplify(diff, max_size):
    # type: (Iterable[Union[Ins, Del, FlushT]], int) -> Iterator[Union[Ins, Del, Replace]]
    previous = None  # type: Union[Ins, Del, Replace, None]
    for token in diff:
        if token is Flush:
            if previous is not None:
                yield previous
                previous = None
            continue

        if previous is None:
            previous = token
            continue

        if isinstance(token, Ins):
            if isinstance(previous, Replace):
                len_previous = len(previous.text)
                if previous.start + len_previous == token.idx:
                    previous = Replace(previous.start, previous.end, previous.text + [token.line])
                    if len_previous >= max_size:
                        yield previous
                        previous = None
                    continue
            elif isinstance(previous, Ins):
                if previous.idx + 1 == token.idx:
                    previous = Replace(previous.idx, previous.idx, [previous.line, token.line])
                    continue
        elif isinstance(token, Del):
            if isinstance(previous, Ins):
                if previous.idx + 1 == token.start:
                    yield Replace(previous.idx, previous.idx + token.end - token.start, [previous.line])
                    previous = None
                    continue
            elif isinstance(previous, Replace):
                if previous.end + len(previous.text) == token.start:
                    yield Replace(previous.start, previous.end + token.end - token.start, previous.text)
                    previous = None
                    continue

        yield previous
        previous = token
        continue

    if previous is not None:
        yield previous


def normalize_tokens(tokens):
    # type: (Iterator[Union[Ins, Del, Replace]]) -> Iterator[Replace]
    for token in tokens:
        if isinstance(token, Ins):
            yield Replace(token.idx, token.idx, [token.line])
        elif isinstance(token, Del):
            yield Replace(token.start, token.end, [])
        else:
            yield token


def apply_diff(a, diff):
    # type: (List[str], Iterable[Union[Ins, Del, Replace]]) -> List[str]
    a = a[:]
    for token in diff:
        if isinstance(token, Replace):
            a[token.start:token.end] = token.text
        elif isinstance(token, Ins):
            a[token.idx:token.idx] = [token.line]
        elif isinstance(token, Del):
            a[token.start:token.end] = []
    return a


ShouldAbort = Callable[[], bool]
Runners = Dict["sublime.BufferId", ShouldAbort]
runners_lock = threading.Lock()
REFRESH_RUNNERS = {}  # type: Runners


def make_aborter(view, store=REFRESH_RUNNERS, _lock=runners_lock):
    # type: (sublime.View, Runners, threading.Lock) -> ShouldAbort
    bid = view.buffer_id()

    def should_abort():
        # type: () -> bool
        if not view.is_valid():
            return True

        with _lock:
            if store[bid] != should_abort:
                return True
        return False

    with _lock:
        store[bid] = should_abort
    return should_abort


def wait_for_first_item(it):
    # type: (Iterable[T]) -> Iterator[T]
    iterable = iter(it)
    head = take(1, iterable)
    return chain(head, iterable)


class Done(Exception):
    pass


_TheEnd = object()


class SimpleFiniteQueue(Generic[T]):
    def __init__(self):
        self._queue: Deque[T] = deque()
        self._count = threading.Semaphore(0)

    def consume(self, it: Iterable[T]) -> None:
        try:
            for item in it:
                self._put(item)
        finally:
            self._put(_TheEnd)  # type: ignore[arg-type]

    def _put(self, item: T) -> None:
        self._queue.append(item)
        self._count.release()

    def get(self, block: bool = True, timeout: float = None) -> T:
        if not self._count.acquire(block, timeout):
            raise Empty
        val = self._queue.popleft()
        if val is _TheEnd:
            raise Done
        else:
            return val


class GraphLine(NamedTuple):
    hash: str
    decoration: str
    subject: str
    info: str
    parents: str


def selection_is_before_region(view, region):
    # type: (sublime.View, sublime.Region) -> bool
    try:
        return view.sel()[-1].end() <= region.end()
    except IndexError:
        return True


class PaintingStateMachine:
    _states = {
        "initial": {"navigated"},
        "navigated": {"viewport_readied"},
        "viewport_readied": set()
    }  # type: Dict[str, Set[str]]

    def __init__(self):
        self._current_state = "initial"

    def __repr__(self):
        return "PaintingStateMachine({})".format(self._current_state)

    def __eq__(self, other):
        # type: (object) -> bool
        if not isinstance(other, str):
            return NotImplemented
        return self._current_state == other

    def set(self, other):
        # type: (str) -> None
        if other not in self._states:
            raise RuntimeError("{} is not a valid state".format(other))
        if other not in self._states[self._current_state]:
            raise RuntimeError(
                "Cannot transition to {} from {}"
                .format(other, self._current_state)
            )
        self._current_state = other


caret_styles = {}  # type: Dict[sublime.View, str]
block_caret_statuses = {}  # type: Dict[sublime.View, bool]
drawn_graph_statuses = {}  # type: Dict[sublime.View, bool]
head_commit_seen = {}  # type: Dict[sublime.View, bool]
LEFT_COLUMN_WIDTH = 82
GRAPH_HEIGHT = 5000
SHOW_ALL_DECORATED_COMMITS = False


def set_caret_style(view, caret_style="smooth"):
    # type: (sublime.View, str) -> None
    start_busy_indicator(view)
    if view not in caret_styles:
        caret_styles[view] = view.settings().get("caret_style")
    view.settings().set("caret_style", caret_style)


def reset_caret_style(view):
    # type: (sublime.View) -> None
    try:
        original_setting = caret_styles[view]
    except KeyError:
        pass
    else:
        view.settings().set("caret_style", original_setting)


def set_block_caret(view):
    # type: (sublime.View) -> None
    start_busy_indicator(view)
    if view not in block_caret_statuses:
        block_caret_statuses[view] = view.settings().get("block_caret")
    view.settings().set("block_caret", True)


def reset_block_caret(view):
    # type: (sublime.View) -> None
    stop_busy_indicator(view)
    try:
        original_setting = block_caret_statuses[view]
    except KeyError:
        pass
    else:
        view.settings().set("block_caret", original_setting)


class BusyIndicators:
    ROLLING = ["◐", "◓", "◑", "◒"]
    STARS = ["◇", "◈", "◆"]
    BRAILLE = "⣷⣯⣟⡿⢿⣻⣽⣾"


@dataclass
class BusyIndicatorConfig:
    start_after: float
    timeout_after: float
    cycle_time: int
    indicators: Sequence[str]


STATUS_BUSY_KEY = "gitsavvy-x-is-busy"
running_busy_indicators: Dict[Tuple[sublime.View, str], BusyIndicatorConfig] = {}


@contextmanager
def busy_indicator(view: sublime.View, status_key: str = STATUS_BUSY_KEY, **options):
    start_busy_indicator(view, status_key, **options)
    try:
        yield
    finally:
        stop_busy_indicator(view, status_key)


def start_busy_indicator(
    view: sublime.View,
    status_key: str = STATUS_BUSY_KEY,
    *,
    start_after: float = 2.0,  # [seconds]
    timeout_after: float = 120.0,  # [seconds]
    cycle_time: int = 200,  # [milliseconds]
    indicators: Sequence[str] = BusyIndicators.ROLLING
) -> None:
    key = (view, status_key)
    is_running = key in running_busy_indicators
    config = BusyIndicatorConfig(start_after, timeout_after, cycle_time, indicators)
    running_busy_indicators[key] = config
    if not is_running:
        _busy_indicator(view, status_key, time.time())


def stop_busy_indicator(view: sublime.View, status_key: str = STATUS_BUSY_KEY) -> None:
    try:
        running_busy_indicators.pop((view, status_key))
    except KeyError:
        pass


def _busy_indicator(view: sublime.View, status_key: str, start_time: float) -> None:
    try:
        config = running_busy_indicators[(view, status_key)]
    except KeyError:
        view.erase_status(status_key)
        return

    now = time.time()
    elapsed = now - start_time
    if config.start_after <= elapsed < config.timeout_after:
        num = len(config.indicators)
        text = config.indicators[int(elapsed * 1000 / config.cycle_time) % num]
        view.set_status(status_key, text)
    else:
        view.erase_status(status_key)

    if elapsed < config.timeout_after and view.is_valid():
        sublime.set_timeout(
            lambda: _busy_indicator(view, status_key, start_time),
            config.cycle_time
        )
    else:
        stop_busy_indicator(view, status_key)


def is_repo_dirty(state):
    # type: (store.RepoStore) -> Optional[bool]
    head_state = state.get("head")
    return not head_state.clean if head_state else None


def remember_drawn_repo_status(view, repo_is_dirty):
    # type: (sublime.View, bool) -> None
    global drawn_graph_statuses
    drawn_graph_statuses[view] = repo_is_dirty


def we_have_seen_the_head_commit(view, seen):
    # type: (sublime.View, bool) -> None
    global head_commit_seen
    head_commit_seen[view] = seen


def on_status_update(repo_path, state):
    # type: (str, store.RepoStore) -> None
    repo_is_dirty = is_repo_dirty(state)
    on_status_update_(repo_path, repo_is_dirty)


@lru_cache(1)
def on_status_update_(repo_path, repo_is_dirty):
    # type: (str, Optional[bool]) -> None
    global drawn_graph_statuses, head_commit_seen
    for view in visible_views():
        if not head_commit_seen.get(view):
            # `gs_log_graph_refresh` is running and has not yet processed HEAD,
            # no need to start all over again.
            continue
        if drawn_graph_statuses.get(view) in (None, repo_is_dirty):
            # The HEAD commit has been drawn with the correct dirty state flag.
            continue

        settings = view.settings()
        if (
            settings.get("git_savvy.log_graph_view")
            and settings.get("git_savvy.repo_path") == repo_path
        ):
            view.run_command("gs_log_graph_refresh")


store.subscribe("*", {"head"}, on_status_update)


def resolve_commit_to_follow_after_rebase(self, commitish):
    # type: (GsTextCommand, str) -> None
    """Resolve a commit after a rebase changed its hash and set to `follow`"""
    # Typically the "commitish" a rebase begins with refers a parent commit
    # and its first child is the actual commit the user is interested in.
    # A typical form is then `abcdef^` if it is not a branch name.
    try:
        to_follow = (
            self.next_commit(commitish)
            or self.git("rev-parse", commitish).strip()
        )
    except GitSavvyError as err:
        # Root commits don't have a parent and so the "^" suffix refers
        # not a valid revision.  Assume "HEAD" is a good position.
        if "fatal: bad revision " in err.stderr:
            to_follow = "HEAD"
        else:
            raise

    if to_follow:
        settings = self.view.settings()
        settings.set("git_savvy.log_graph_view.follow", self.get_short_hash(to_follow))


class gs_log_graph_refresh(GsTextCommand):

    """
    Refresh the current graph view with the latest commits.
    """

    def run(self, edit, assume_complete_redraw=False):
        # type: (object, bool) -> None
        # Edge case: If you restore a workspace/project, the view might still be
        # loading and hence not ready for refresh calls.
        if self.view.is_loading():
            return

        parent_commitish = self.view.settings().get("git_savvy.resolve_after_rebase")
        if parent_commitish and not self.in_rebase():
            self.view.settings().erase("git_savvy.resolve_after_rebase")
            resolve_commit_to_follow_after_rebase(self, parent_commitish)

        initial_draw = self.view.size() == 0
        prelude_text = prelude(self.view)
        if initial_draw or assume_complete_redraw:
            prelude_region = (
                None
                if initial_draw else
                self.view.find_by_selector('meta.prelude.git_savvy.graph')[0]
            )
            replace_view_content(self.view, prelude_text, prelude_region)

        should_abort = make_aborter(self.view)
        # Set flag that we started the refresh process.  This must be in sync with the later
        # `awaiting_head_commit` *local* variable.
        we_have_seen_the_head_commit(self.view, False)
        enqueue_on_worker(
            self.run_impl,
            initial_draw,
            assume_complete_redraw,
            prelude_text,
            should_abort,
        )

    def run_impl(
        self,
        initial_draw,
        assume_complete_redraw,
        prelude_text,
        should_abort,
    ):
        # type: (bool, bool, str, ShouldAbort) -> None
        settings = self.view.settings()
        try:
            # In case of `assume_complete_redraw` we later clear the graph content
            # so we assume `""` for that case.
            # See usage of `clear_graph()`.
            current_graph = self.view.substr(
                self.view.find_by_selector('meta.content.git_savvy.graph')[0]
            ) if not assume_complete_redraw else ""
        except IndexError:
            current_graph_splitted = []
        else:
            current_graph_splitted = current_graph.splitlines(keepends=True)

        token_queue = SimpleFiniteQueue()  # type: SimpleFiniteQueue[Replace]
        current_proc = None
        graph_offset = len(prelude_text)

        def remember_proc(proc):
            # type: (subprocess.Popen) -> None
            nonlocal current_proc
            current_proc = proc

        def ensure_not_aborted(fn):
            def decorated(*args, **kwargs):
                if should_abort():
                    utils.try_kill_proc(current_proc)
                else:
                    return fn(*args, **kwargs)
            return decorated

        def split_up_line(line):
            # type: (str) -> Union[str, GraphLine]
            try:
                return GraphLine(*line.rstrip().split("%00"))
            except TypeError:
                return line

        def line_matches(needle, line):
            # type: (str, Union[str, GraphLine]) -> bool
            if isinstance(line, str):
                return False
            if needle == "HEAD":
                return (
                    needle == line.decoration
                    or line.decoration.startswith(f"{needle} ->")
                )
            return (
                needle in line.hash
                or needle == line.decoration
                or f" {needle}" in line.decoration
                or f"{needle}," in line.decoration
            )

        def process_graph(lines):
            # type: (Iterable[Union[str, GraphLine]]) -> Iterator[Union[str, GraphLine]]
            """
            Generally limit number of commits we show.

            Typically `follow` is set and where the cursor either lands or already is.
            Draw every line until we find the symbol we "follow", and then some more,
            defined in `FOLLOW_UP`.

            If `follow` cannot be found in the graph, that happens rather often, e.g. when
            you dynamically filter the graph or change which branches it shows, draw
            as many lines as before (`default_number_of_commits_to_show`).
            """
            FOLLOW_UP = GRAPH_HEIGHT
            current_number_of_commits = (
                self.view.rowcol(self.view.size())[0]
                - prelude_text.count("\n")
                - 1  # trailing newline
            )
            default_number_of_commits_to_show = max(FOLLOW_UP, current_number_of_commits)
            follow = settings.get('git_savvy.log_graph_view.follow')
            if not follow:
                yield from islice(lines, default_number_of_commits_to_show)
                return

            stop_after_idx = None  # type: Optional[int]
            """The `Optional` in `stop_after_idx` holds our state-machine.
            `None` denotes we're still searching for `follow`, `not None`
            that we have found it.  The `int` type then tells us at which line
            we stop the graph.
            """
            queued_lines = []  # type: List[Union[str, GraphLine]]
            """Holds all lines we cannot immediately draw because they're after
            `default_number_of_commits_to_show`.  We need to remember them
            in case we still find `follow`.
            """

            for idx, line in enumerate(lines):
                if stop_after_idx is None:
                    if line_matches(follow, line):
                        stop_after_idx = idx + FOLLOW_UP
                        yield from queued_lines
                        yield line
                    else:
                        if idx < default_number_of_commits_to_show:
                            yield line
                        else:
                            queued_lines.append(line)
                else:
                    if idx < stop_after_idx:
                        yield line
                    elif SHOW_ALL_DECORATED_COMMITS:
                        if not isinstance(line, str) and line.decoration:
                            yield "...\n"
                            yield line
                    else:
                        utils.try_kill_proc(current_proc)
                        yield "..."
                        break

            if SHOW_ALL_DECORATED_COMMITS:
                try:
                    line
                except NameError:  # `lines` was empty
                    pass
                else:
                    if isinstance(line, str) or not line.decoration:
                        yield "...\n"

        def trunc(text, width):
            # type: (str, int) -> str
            return f"{text[:width - 2]}.." if len(text) > width else f"{text:{width}}"

        def resolve_refs_from_the_logs():
            # type: () -> Dict[str, str]
            # git does not decorate refs from the reflogs, e.g. "branch@{2}", so we resolve
            # them manually.
            all_branches = settings.get("git_savvy.log_graph_view.all_branches")
            applying_filters = settings.get("git_savvy.log_graph_view.apply_filters")
            additional_args = " ".join((
                (
                    settings.get("git_savvy.log_graph_view.filters", "")
                    if applying_filters
                    else ""
                ),
                " ".join(
                    []
                    if all_branches
                    else settings.get("git_savvy.log_graph_view.branches", [])
                )
            ))
            requested_refs = re.findall(r"\S+@{\d+}", additional_args)
            return {
                commit_hash: ref

                for branch_name, refs in groupby(
                    sorted(requested_refs),
                    key=lambda ref: ref.split("@")[0]
                )
                if (wanted_refs := list(refs))

                for n, commit_hash in enumerate(reversed([
                    self.get_short_hash(line.split(maxsplit=2)[1])
                    for line in self._read_git_file("logs", "refs", "heads", branch_name).splitlines()
                ]))
                if (ref := f"{branch_name}@{{{n}}}") in wanted_refs
            }

        ASCII_ART_LENGHT_LIMIT = 48
        SHORTENED_ASCII_ART = ".. / \n"
        in_overview_mode = settings.get("git_savvy.log_graph_view.overview")
        awaiting_head_commit = True
        additional_decorations = resolve_refs_from_the_logs()

        def simplify_decoration(decoration):
            # type: (str) -> str
            """Simplify decoration by omitting remote branches if they match
            local branches.
            """
            decoration_parts = decoration.split(", ")
            decoration_parts_ = [
                d[8:] if d.startswith("HEAD -> ") else
                d[9:] if d.startswith("HEAD* -> ") else
                d for d in decoration_parts
            ]
            return ", ".join(
                d for d in decoration_parts
                if "/" not in d or d[d.index("/") + 1:] not in decoration_parts_
            )

        def format_line(line):
            # type: (Union[str, GraphLine]) -> str
            nonlocal awaiting_head_commit
            if isinstance(line, str):
                if len(line) > ASCII_ART_LENGHT_LIMIT:
                    return SHORTENED_ASCII_ART
                return line

            hash, decoration, subject, info, parents = line
            if parents:
                hash = hash.replace("*", DEFAULT_NODE_CHAR, 1)
            else:
                hash = hash.replace("*", ROOT_NODE_CHAR, 1)
            if (
                len(hash) > ASCII_ART_LENGHT_LIMIT
                or in_overview_mode
                or additional_decorations
            ):
                commit_hash = hash.rsplit(" ", 1)[1]
                if len(hash) > ASCII_ART_LENGHT_LIMIT:
                    hash = f".. {DEFAULT_NODE_CHAR} {commit_hash}"
                elif in_overview_mode:
                    hash = hash.ljust(len(commit_hash) + 6)

                if commit_hash in additional_decorations:
                    ref = additional_decorations.pop(commit_hash)
                    decoration = ", ".join(filter_((decoration, ref)))

            if decoration:
                if awaiting_head_commit and (
                    decoration == "HEAD"
                    or decoration.startswith("HEAD ->")
                    or decoration.startswith("HEAD, ")
                ):
                    awaiting_head_commit = False
                    we_have_seen_the_head_commit(self.view, True)
                    repo_is_dirty = is_repo_dirty(self.current_state())
                    if repo_is_dirty:
                        decoration = decoration.replace("HEAD", "HEAD*", 1)
                    remember_drawn_repo_status(self.view, bool(repo_is_dirty))
                if in_overview_mode:
                    decoration = simplify_decoration(decoration)
                left = f"{hash} ({decoration})"
            else:
                left = f"{hash}"
            return f"{left} {trunc(subject, max(2, LEFT_COLUMN_WIDTH - len(left)))} \u200b {info}\n"

        def filter_consecutive_continuation_lines(lines):
            # type: (Iterator[str]) -> Iterator[str]
            for left, right in pairwise(chain([""], lines)):
                if right == SHORTENED_ASCII_ART and left == right:
                    continue
                if (
                    left.startswith(ROOT_NODE_CHAR)
                    and right.strip()
                    # Check if we already had clear continuations in the graph
                    # art, e.g. "⌂ | | 024cfad"
                    and (match := COMMIT_LINE.search(left))
                    and match.span("commit_hash")[0] < 4
                ):
                    yield "-\n"
                yield right

        def clear_graph():
            if should_abort():
                return

            try:
                content_region = self.view.find_by_selector("meta.content.git_savvy.graph")[0]
            except IndexError:
                pass
            else:
                replace_view_content(self.view, "", content_region)
                self.view.set_viewport_position((0, 0))
                set_block_caret(self.view)

        def indicate_slow_progress():
            set_caret_style(self.view)

        def reader():
            graph = self.read_graph(got_proc=remember_proc)
            if (
                initial_draw
                and settings.get('git_savvy.log_graph_view.decoration') == 'sparse'
            ):
                # On large repos (e.g. the "git" repo) "--sparse" can be excessive to compute
                # upfront t.i. before the first byte. For now, just race with a timeout and
                # maybe fallback.
                try:
                    lines = run_or_timeout(lambda: wait_for_first_item(graph), timeout=1.0)
                except TimeoutError:
                    utils.try_kill_proc(current_proc)
                    settings.set('git_savvy.log_graph_view.decoration', None)
                    enqueue_on_worker(
                        self.view.run_command,
                        "gs_log_graph_refresh",
                        {"assume_complete_redraw": True}
                    )
                    return
            else:
                lines = run_and_check_timeout(
                    lambda: wait_for_first_item(graph),
                    timeout=0.1,
                    callback=(
                        [clear_graph, indicate_slow_progress]
                        if assume_complete_redraw
                        else indicate_slow_progress
                    )
                )
            next_graph_splitted = filter_consecutive_continuation_lines(chain(
                map(
                    format_line,
                    process_graph(
                        map(split_up_line, lines)
                    )
                ),
                ['\n']
            ))
            tokens = normalize_tokens(simplify(
                diff(current_graph_splitted, next_graph_splitted),
                max_size=100
            ))

            # Do not switch to the UI thread before we have a token ready for
            # render.  Maybe the graph is even up-to-date and there ain't no
            # tokens to draw.
            tokens = wait_for_first_item(tokens)
            enqueue_on_ui(draw)
            token_queue.consume(tokens)

        @ensure_not_aborted
        def draw():
            set_block_caret(self.view)
            sel = get_simple_selection(self.view)
            current_prelude_region = self.view.find_by_selector('meta.prelude.git_savvy.graph')[0]

            # Usually the cursor is set to the symbol at `follow`.  The cursor "follows" this
            # symbol so to speak.
            if (
                # If the user has a "complex", e.g. multi-line or multi-cursor, selection, we
                # temporarily do *not* follow as it would destroy their selection.
                sel is None

                # Do not move the cursor as well for simple selections
                or (
                    # *if* they're in the prelude
                    sel in current_prelude_region
                    # except if `initial_draw` or `assume_complete_redraw` is set because then
                    # the cursor is in the prelude just because nothing else is drawn yet.
                    and not (initial_draw or assume_complete_redraw)
                )
            ):
                follow, col_range = None, None
            else:
                follow = settings.get('git_savvy.log_graph_view.follow')
                col_range = get_column_range(self.view, sel)

            visible_selection = is_sel_in_viewport(self.view)

            replace_view_content(self.view, prelude_text, current_prelude_region)
            if assume_complete_redraw:
                clear_graph()
            drain_and_draw_queue(self.view, PaintingStateMachine(), follow, col_range, visible_selection)

        # Sublime will not run any event handlers until the (outermost) TextCommand exits.
        # T.i. the (inner) commands `replace_view_content` and `set_and_show_cursor` will run
        # through uninterrupted until `drain_and_draw_queue` yields. Then e.g.
        # `on_selection_modified` runs *once* even if we painted multiple times.
        @ensure_not_aborted
        @text_command
        def drain_and_draw_queue(view, painter_state, follow, col_range, visible_selection):
            # type: (sublime.View, PaintingStateMachine, Optional[str], Optional[Tuple[int, int]], bool) -> None
            call_again = partial(
                drain_and_draw_queue,
                view,
                painter_state,
                follow,
                col_range,
                visible_selection,
            )

            if not follow:
                def try_navigate_to_symbol(*, if_before=None) -> bool:
                    return False
            else:
                try_navigate_to_symbol = partial(
                    navigate_to_symbol,
                    view,
                    follow,
                    col_range=col_range,
                    method=set_and_show_cursor if visible_selection else just_set_cursor
                )

            block_time = utils.timer()
            while True:
                # If only the head commits changed, and the cursor (and with it `follow`)
                # is a few lines below, the `if_before=region` will probably never catch.
                # We would block here 'til TheEnd without a timeout.
                try:
                    token = token_queue.get(
                        block=True if painter_state != 'viewport_readied' else False,
                        timeout=0.05 if painter_state != 'viewport_readied' else None
                    )
                except Empty:
                    enqueue_on_worker(call_again)
                    return
                except Done:
                    break

                region = apply_token(view, token, graph_offset)

                if painter_state == 'initial':
                    if follow:
                        if try_navigate_to_symbol(if_before=region):
                            painter_state.set('navigated')
                    elif initial_draw:
                        view.run_command("gs_log_graph_navigate")
                        painter_state.set('navigated')
                    elif selection_is_before_region(view, region):
                        painter_state.set('navigated')

                if painter_state == 'navigated':
                    if region.end() >= view.visible_region().end():
                        painter_state.set('viewport_readied')
                    reset_block_caret(view)

                if block_time.passed(13 if painter_state == 'viewport_readied' else 1000):
                    enqueue_on_worker(call_again)
                    return

            if painter_state == 'initial':
                # If we still did not navigate the symbol is either
                # gone, or happens to be after the fold of fresh
                # content.
                if not try_navigate_to_symbol():
                    if initial_draw:
                        view.run_command("gs_log_graph_navigate")
                    elif visible_selection:
                        view.show(view.sel(), True)
            reset_block_caret(view)
            reset_caret_style(view)
            enqueue_on_worker(view.clear_undo_stack)

        def apply_token(view, token, offset):
            # type: (sublime.View, Replace, int) -> sublime.Region
            nonlocal current_graph_splitted
            start, end, text_ = token
            text = ''.join(text_)
            computed_start = (
                sum(len(line) for line in current_graph_splitted[:start])
                + offset
            )
            computed_end = (
                sum(len(line) for line in current_graph_splitted[start:end])
                + computed_start
            )
            region = sublime.Region(computed_start, computed_end)

            current_graph_splitted = apply_diff(current_graph_splitted, [token])
            replace_view_content(view, text, region)
            occupied_space = sublime.Region(computed_start, computed_start + len(text))
            return occupied_space

        run_on_new_thread(reader)

    def read_graph(self, got_proc=None):
        # type: (Callable[[subprocess.Popen], None]) -> Iterator[str]
        args = self.build_git_command()
        yield from self.git_streaming(*args, got_proc=got_proc)

    def build_git_command(self):
        settings = self.view.settings()
        filters = settings.get("git_savvy.log_graph_view.filters")
        apply_filters = settings.get("git_savvy.log_graph_view.apply_filters")
        overview = settings.get("git_savvy.log_graph_view.overview")
        if overview:
            show_tags = settings.get("git_savvy.log_graph_view.show_tags")
            args = [
                'log',
                '--graph',
                '--decorate',  # set explicitly for "decorate-refs-exclude" to work
                '--decorate-refs-exclude=refs/remotes/origin/HEAD',  # cosmetics
                '--decorate-refs-exclude=refs/tags' if not show_tags else None,
                '--date=format:%b %e %Y',
                '--format={}'.format(
                    "%00".join(
                        ("%h", "%D", "", "%ad, %an", "%p")
                    )
                ),
                '--date-order',
                '--exclude=refs/stash',
                '--all',
                '--simplify-by-decoration',
            ]
            if filters and apply_filters:
                args += shlex.split(filters)

            return args

        follow = self.savvy_settings.get("log_follow_rename")
        all_branches = settings.get("git_savvy.log_graph_view.all_branches")
        author = settings.get("git_savvy.log_graph_view.filter_by_author")
        branches = settings.get("git_savvy.log_graph_view.branches")
        paths = settings.get("git_savvy.log_graph_view.paths", [])  # type: List[str]
        date_format = (
            "human"
            if self.git_version >= GIT_SUPPORTS_HUMAN_DATE_FORMAT
            else FALLBACK_DATE_FORMAT
        )
        args = [
            'log',
            '--graph',
            '--decorate',  # set explicitly for "decorate-refs-exclude" to work
            '--date={}'.format(date_format),
            '--format={}'.format(
                "%00".join(
                    ("%h", "%D", "%s", "%ad, %an", "%p")
                )
            ),
            # Git can only follow exactly one path.  Luckily, this can
            # be a file or a directory.
            '--follow' if len(paths) == 1 and follow and apply_filters else None,
            '--author={}'.format(author) if author and apply_filters else None,
            '--decorate-refs-exclude=refs/remotes/origin/HEAD',  # cosmetics
            '--exclude=refs/stash',
            '--all' if all_branches else None,
        ]

        if (
            (not paths or not apply_filters)
            and settings.get('git_savvy.log_graph_view.decoration') == 'sparse'
        ):
            args += ['--simplify-by-decoration', '--sparse']

        if branches and not all_branches:
            args += branches

        if filters and apply_filters:
            args += shlex.split(filters)

        if paths and apply_filters:
            args += ["--"] + paths

        return args


def prelude(view):
    # type: (sublime.View) -> str
    settings = view.settings()
    all_branches = settings.get("git_savvy.log_graph_view.all_branches") or False
    apply_filters = settings.get("git_savvy.log_graph_view.apply_filters")
    branches = settings.get("git_savvy.log_graph_view.branches") or []
    filters = settings.get("git_savvy.log_graph_view.filters") or ""
    overview = settings.get("git_savvy.log_graph_view.overview")
    paths = settings.get("git_savvy.log_graph_view.paths") or []
    repo_path = settings.get("git_savvy.repo_path")

    prelude = "\n"
    if paths and apply_filters and not overview:
        prelude += "  FILE: {}\n".format(" ".join(paths))
    elif repo_path:
        prelude += "  REPO: {}\n".format(repo_path)

    if apply_filters:
        pickaxes, normal_ones = [], []
        for arg in shlex.split(filters):
            if arg.startswith("-S") or arg.startswith("-G"):
                if "\n" in arg:
                    pickaxes.append(
                        "\n  {}'''\n{}\n  '''".format(
                            arg[:2],
                            textwrap.indent(textwrap.dedent(arg[2:].rstrip()), "    ")
                        )
                    )
                else:
                    normal_ones.append(
                        "{}'{}'".format(
                            arg[:2],
                            arg[3:-1] if (arg[2], arg[-1]) == ("'", "'") else arg[2:]
                        )
                    )
            else:
                normal_ones.append(arg)
        formatted_filters = "\n".join(filter_((" ".join(normal_ones), "".join(pickaxes))))
    else:
        formatted_filters = None

    if not all_branches and not overview:
        formatted_branches = " ".join(branches)
    else:
        formatted_branches = None

    prelude += (
        "  "
        + "  ".join(filter_((
            (
                'OVERVIEW'
                if overview
                else '[a]ll: true' if all_branches else '[a]ll: false'
            ),
            formatted_branches,
            formatted_filters
        )))
    )
    return prelude + "\n\n"


class gs_log_graph_tab_out(GsTextCommand):
    def run(self, edit, reverse=False):
        options = self.current_state().get("default_graph_options", {})
        options.update({
            "all": self.view.settings().get("git_savvy.log_graph_view.all_branches")
        })
        self.update_store({"default_graph_options": options})
        self.view.settings().set("git_savvy.log_graph_view.default_graph", True)
        for view_ in self.window.views():
            if (
                view_ != self.view
                and (settings := view_.settings())
                and settings.get("git_savvy.log_graph_view.default_graph")
                and settings.get("git_savvy.repo_path") == self.repo_path
            ):
                settings.erase("git_savvy.log_graph_view.default_graph")

        self.view.run_command("gs_tab_cycle", {"source": "graph", "reverse": reverse})


class gs_log_graph_tab_in(WindowCommand, GitCommand):
    def run(self, file_path=None):
        for view in self.window.views():
            settings = view.settings()
            if (
                settings.get("git_savvy.log_graph_view.default_graph")
                and settings.get("git_savvy.repo_path") == self.repo_path
            ):
                focus_view(view)
                return

        all_branches = (
            self.current_state()
            .get("default_graph_options", {})
            .get("all", True)
        )
        self.window.run_command('gs_graph', {
            'file_path': file_path,
            'all': all_branches,
        })


class gs_log_graph(gs_log):
    """
    Defines the main menu if you invoke `git: graph` or `git: graph current file`.

    Accepts `current_file: bool` or `file_path: str` as (keyword) arguments, and
    ensures that each of the defined actions/commands in `default_actions` are finally
    called with `file_path` set.
    """
    default_actions = [
        ["gs_log_graph_current_branch", "For current branch"],
        ["gs_log_graph_all_branches", "For all branches"],
        ["gs_log_graph_by_branch", "For a specific branch..."],
        ["gs_log_graph_by_author", "Filtered by author..."],
    ]


class gs_log_graph_current_branch(WindowCommand, GitCommand):
    def run(self, file_path=None):
        branches_to_show = self.compute_branches_to_show("HEAD")
        self.window.run_command('gs_graph', {
            'file_path': file_path,
            'all': False,
            'branches': branches_to_show,
        })


class gs_log_graph_all_branches(WindowCommand, GitCommand):
    def run(self, file_path=None):
        self.window.run_command('gs_graph', {
            'file_path': file_path,
            'all': True,
        })


class gs_log_graph_by_author(WindowCommand, GitCommand):

    """
    Open a quick panel containing all committers for the active
    repository, ordered by most commits, Git name, and email.
    Once selected, display a quick panel with all commits made
    by the specified author.
    """

    def run(self, file_path=None):
        commiter_str = self.git("shortlog", "-sne", "HEAD")
        entries = []
        for line in commiter_str.split('\n'):
            m = re.search(r'\s*(\d*)\s*(.*)\s<(.*)>', line)
            if m is None:
                continue
            commit_count, author_name, author_email = m.groups()
            author_text = "{} <{}>".format(author_name, author_email)
            entries.append((commit_count, author_name, author_email, author_text))

        def on_select(index):
            if index == -1:
                return
            selected_author = entries[index][3]
            self.window.run_command('gs_graph', {
                'file_path': file_path,
                'author': selected_author
            })

        email = self.git("config", "user.email").strip()
        self.window.show_quick_panel(
            [entry[3] for entry in entries],
            on_select,
            flags=sublime.MONOSPACE_FONT,
            selected_index=[line[2] for line in entries].index(email)
        )


class gs_log_graph_by_branch(WindowCommand, GitCommand):
    _selected_branch = None

    def run(self, file_path=None):
        def on_select(branch):
            self._selected_branch = branch  # remember last selection
            branches_to_show = self.compute_branches_to_show(branch)
            self.window.run_command('gs_graph', {
                'file_path': file_path,
                'all': False,
                'branches': branches_to_show,
                'follow': branch,
            })

        show_branch_panel(on_select, selected_branch=self._selected_branch)


class gs_log_graph_navigate(TextCommand):
    def run(self, edit, forward=True, natural_movement=False):
        sel = self.view.sel()
        current_position = max(
            sel[0].a,
            # If inside the prelude section, jump to the *first*
            # commit.  For `.b`, Sublime already returns the first
            # row of the content section, thus `- 1` to compensate.
            find_by_selector(self.view, "meta.prelude")[0].b - 1
        )

        wanted_section = self.search(current_position, forward, natural_movement)
        if wanted_section is None:
            if natural_movement:
                self.view.run_command("move", {"by": "lines", "forward": forward})
            return

        sel.clear()
        sel.add(wanted_section.begin())
        show_region(self.view, wanted_section)

    def search(self, current_position, forwards=True, natural_movement=False):
        # type: (sublime.Point, bool, bool) -> Optional[sublime.Region]
        view = self.view
        row, col = view.rowcol(current_position)
        rows = count(row + 1, 1) if forwards else count(row - 1, -1)
        for row_ in rows:
            line = line_from_pt(view, view.text_point(row_, 0))
            if len(line) == 0:
                break

            commit_hash_region = extract_comit_hash_span(view, line)
            if not commit_hash_region:
                continue

            if not natural_movement:
                return commit_hash_region

            col_ = commit_hash_region.b - line.a
            if col <= col_:
                return commit_hash_region
            else:
                return sublime.Region(view.text_point(row_, col))
        return None


class gs_log_graph_navigate_wide(TextCommand):
    def run(self, edit, forward=True):
        # type: (sublime.Edit, bool) -> None
        view = self.view
        try:
            cur_dot = next(chain(find_graph_art_at_cursor(view), _find_dots(view)))
        except StopIteration:
            view.run_command("gs_log_graph_navigate", {"forward": forward})
            return

        next_dots = follow_first_parent(cur_dot, forward)
        try:
            next_dot = next(next_dots)
        except StopIteration:
            return

        if line_distance(view, cur_dot.region(), next_dot.region()) < 2:
            # If the first next dot is not already a wide jump, t.i. the
            # cursor is not on an edge commit, follow the chain of consecutive
            # commits and select the last one of such a block.  T.i. select
            # the commit *before* the next wide jump.
            for next_dot, next_next_dot in pairwise(chain([next_dot], next_dots)):
                if line_distance(view, next_dot.region(), next_next_dot.region()) > 1:
                    break
            else:
                # If there is no wide jump anymore take the last found dot.
                # This is the case for example a the top of the graph, or if
                # a branch ends.
                # Catch if there is no next_next_dot (t.i. `next_dots` was empty),
                # then the last found dot is actually `next_dot`.
                try:
                    next_dot = next_next_dot
                except UnboundLocalError:
                    pass

        line = line_from_pt(view, next_dot.region())
        r = extract_comit_hash_span(view, line)
        if r:
            add_selection_to_jump_history(view)
            sel = view.sel()
            sel.clear()
            sel.add(r.begin())
            show_region(
                view,
                join_regions(cur_dot.region(), r),
                prefer_end=True if forward else False
            )


def follow_first_parent(dot, forward=True):
    # type: (colorizer.Char, bool) -> Iterator[colorizer.Char]
    """Follow left (first-parent) dot to dot omitting the path chars in between."""
    while True:
        try:
            dot = next(dots_after_dot(dot, forward))
        except StopIteration:
            return
        else:
            yield dot


def follow_dots(dot, forward=True):
    # type: (colorizer.Char, bool) -> Iterator[colorizer.Char]
    """Breadth first traverse dot to dot."""
    # Always sort by `dot.pt` to keep the order exactly like the visual
    # order in the view.
    stack = sorted(dots_after_dot(dot, forward), key=lambda dot: dot.pt, reverse=not forward)
    seen = set()
    while stack:
        dot = stack.pop(0)
        if dot not in seen:
            yield dot
            seen.add(dot)
            stack.extend(dots_after_dot(dot, forward))
            stack.sort(key=lambda dot: dot.pt, reverse=not forward)


def dots_after_dot(dot, forward=True):
    # type: (colorizer.Char, bool) -> Iterator[colorizer.Char]
    """Return exact next dots (commits) after `dot`."""
    fn = colorizer.follow_path_down if forward else colorizer.follow_path_up
    return filter(lambda ch: ch.char() in colorizer.COMMIT_NODE_CHARS, fn(dot))


class gs_log_graph_navigate_to_head(TextCommand):

    """
    Travel to the HEAD commit.
    """

    def run(self, edit):
        try:
            head_commit = self.view.find_by_selector(
                "git-savvy.graph meta.graph.graph-line.head.git-savvy "
                "constant.numeric.graph.commit-hash.git-savvy"
            )[0]
        except IndexError:
            settings = self.view.settings()
            settings.set("git_savvy.log_graph_view.all_branches", True)
            settings.set("git_savvy.log_graph_view.follow", "HEAD")
            self.view.run_command("gs_log_graph_refresh")
        else:
            set_and_show_cursor(self.view, head_commit.begin())


class gs_log_graph_edit_branches(TextCommand):
    def run(self, edit):
        settings = self.view.settings()
        branches = settings.get("git_savvy.log_graph_view.branches", [])  # type: List[str]

        def on_done(text):
            # type: (str) -> None
            new_branches = list(filter_(text.split(' ')))
            settings.set("git_savvy.log_graph_view.branches", new_branches)
            self.view.run_command("gs_log_graph_refresh")

        show_single_line_input_panel(
            "branches", ' '.join(branches), on_done, select_text=True
        )


DEFAULT_HISTORY_ENTRIES = ["--date-order", "--dense", "--first-parent", "--reflog"]


class gs_log_graph_edit_filters(TextCommand):
    def run(self, edit):
        # type: (sublime.Edit) -> None
        view = self.view
        settings = view.settings()
        applying_filters = settings.get("git_savvy.log_graph_view.apply_filters")
        filters = (
            settings.get("git_savvy.log_graph_view.filters", "")
            if applying_filters
            else ""
        )
        filter_history = settings.get("git_savvy.log_graph_view.filter_history")
        if not filter_history:
            filter_history = DEFAULT_HISTORY_ENTRIES + ([filters] if filters else [])

        author_tip = self.get_author_tip()
        history_entries = (
            (
                [author_tip]
                if author_tip and author_tip not in filter_history
                else []
            )
            + filter_history
        )
        virtual_entries_count = len(history_entries) - len(filter_history)
        active = index_of(history_entries, filters, -1)

        def on_done(text):
            # type: (str) -> None
            if not text:
                # A user can delete entries from the history by selecting
                # an entry, deleting the contents, and finally hitting `enter`.
                # Note that `active` can be "-1" (often the default), denoting
                # the imaginary empty last entry. It is also offset since we may
                # have added "virtual" entries (the `author_tip`) at the top of it
                # which cannot be deleted, just like the `DEFAULT_HISTORY_ENTRIES`.
                new_active = (
                    input_panel_settings.get("input_panel_with_history.active")
                    - virtual_entries_count
                )
                if 0 <= new_active < len(filter_history):
                    if filter_history[new_active] not in DEFAULT_HISTORY_ENTRIES:
                        filter_history.pop(new_active)

            new_filter_history = (
                filter_history
                if text in filter_history or not text
                else (filter_history + [text])
            )
            settings.set("git_savvy.log_graph_view.apply_filters", True)
            settings.set("git_savvy.log_graph_view.filters", text)
            settings.set("git_savvy.log_graph_view.filter_history", new_filter_history)
            if not applying_filters:
                settings.set("git_savvy.log_graph_view.paths", [])
                settings.set("git_savvy.log_graph_view.filter_by_author", "")

            hide_toast()
            view.run_command("gs_log_graph_refresh", {"assume_complete_redraw": bool(text)})

        def on_cancel():
            enqueue_on_worker(hide_toast)

        input_panel = show_single_line_input_panel(
            "additional args",
            filters,
            on_done,
            on_cancel=on_cancel,
            select_text=True
        )
        input_panel_settings = input_panel.settings()
        input_panel_settings.set("input_panel_with_history", True)
        input_panel_settings.set("input_panel_with_history.entries", history_entries)
        input_panel_settings.set("input_panel_with_history.active", active)

        hide_toast = show_toast(
            view,
            "↑↓ for the history\n"
            "Examples:  -Ssearch_term  |  -Gsearch_term  ",
            timeout=-1
        )

    def get_author_tip(self):
        # type: () -> str
        view = self.view
        line_span = view.line(view.sel()[0].b)
        for r in find_by_selector(view, "entity.name.tag.author.git-savvy"):
            if line_span.intersects(r):
                return "--author='{}' -i".format(view.substr(r).strip())
        return ""


def index_of(seq, needle, default):
    # type: (Sequence[T], T, int) -> int
    try:
        return seq.index(needle)
    except ValueError:
        return default


class gs_input_handler_go_history(TextCommand):
    def run(self, edit, forward=True):
        # type: (sublime.Edit, bool) -> None
        # In the case of an input handler, `self.view.settings` is cached
        # and returns stale answers.  We work-around by recreating the
        # `view` object which in turn recreates the `settings` object freshly.
        view = sublime.View(self.view.id())
        settings = view.settings()
        history = settings.get("input_panel_with_history.entries")
        if not history:
            return

        len_history = len(history)
        active = settings.get("input_panel_with_history.active", -1)
        if active == -1:
            active = len_history

        if forward:
            active += 1
        else:
            active -= 1

        active = max(0, min(len_history, active))
        text = history[active] if active < len_history else ""
        replace_view_content(view, text)
        view.run_command("move_to", {"to": "eol", "extend": False})
        view.settings().set("input_panel_with_history.active", active)

        show_toast(
            view,
            "\n".join(
                "   {}".format(entry) if idx != active else ">  {}".format(entry)
                for idx, entry in enumerate(history + [""])
            ),
            timeout=2500
        )


class gs_log_graph_reset_filters(TextCommand):
    def run(self, edit):
        settings = self.view.settings()
        current = settings.get("git_savvy.log_graph_view.apply_filters")
        next_state = not current
        settings.set("git_savvy.log_graph_view.apply_filters", next_state)
        self.view.run_command("gs_log_graph_refresh", {"assume_complete_redraw": next_state})


class gs_log_graph_toggle_overview(TextCommand):
    def run(self, edit):
        view = self.view
        settings = view.settings()
        current = settings.get("git_savvy.log_graph_view.overview")
        next_state = not current
        if next_state:
            follow = settings.get("git_savvy.log_graph_view.follow")
            symbols = view.symbols()
            symbols_ = {s for _, s in symbols}
            if follow not in symbols_:
                dots = find_dots(view)
                if len(dots) == 1:
                    dot = dots.pop()
                    s = next_symbol_upwards(view, symbols, dot)
                    settings.set("git_savvy.log_graph_view.follow", s)

        settings.set("git_savvy.log_graph_view.overview", next_state)
        self.view.run_command("gs_log_graph_refresh", {"assume_complete_redraw": True})


def next_symbol_upwards(view, symbols, dot):
    # type: (sublime.View, List[Tuple[sublime.Region, str]], colorizer.Char) -> Optional[str]
    previous_dots = follow_dots(dot, forward=False)
    for dot in take(50, previous_dots):
        line_span = view.line(dot.pt)
        # Capture all symbols from the line, ...
        symbols_on_line = []
        for r, s in symbols:
            if line_span.a <= r.a <= line_span.b:
                symbols_on_line.append(s)
            if r.a > line_span.b:
                break
        # ... and choose the last one as git puts remote branches first.
        if symbols_on_line:
            return symbols_on_line[-1]
    return None


class gs_log_graph_edit_files(TextCommand, GitCommand):
    def run(self, edit, selected_index=-1):
        view = self.view
        settings = view.settings()
        window = view.window()
        assert window

        files = self.list_controlled_files(view.change_count())
        apply_filters = settings.get("git_savvy.log_graph_view.apply_filters")
        paths = (
            settings.get("git_savvy.log_graph_view.paths", [])
            if apply_filters
            else []
        )  # type: List[str]
        items = (
            [
                ">  {}".format(file)
                for file in paths
            ]
            +
            sorted(
                "   {}".format(file)
                for file in chain(files, set(os.path.dirname(f) for f in files))
                if file and file not in paths
            )
        )

        def on_done(idx, event={}):
            if idx < 0:
                return

            selected = items[idx]
            unselect = selected[0] == ">"
            path = selected[3:]
            if unselect:
                next_paths = [p for p in paths if p != path]
            else:
                next_paths = paths + [path]

            settings.set("git_savvy.log_graph_view.paths", next_paths)
            settings.set("git_savvy.log_graph_view.apply_filters", True)
            if not apply_filters:
                settings.set("git_savvy.log_graph_view.filters", "")
                settings.set("git_savvy.log_graph_view.filter_by_author", "")
            view.run_command("gs_log_graph_refresh", {"assume_complete_redraw": bool(next_paths)})
            if event.get("modifier_keys", {}).get("primary"):
                view.run_command("gs_log_graph_edit_files", {
                    "selected_index": max(0, idx - 1) if unselect else next_paths.index(path)
                })

        window.show_quick_panel(
            items,
            on_done,
            flags=sublime.MONOSPACE_FONT | (
                sublime.WANT_EVENT if QUICK_PANEL_SUPPORTS_WANT_EVENT else 0
            ),
            selected_index=selected_index
        )

    @lru_cache(1)
    def list_controlled_files(self, __cc):
        # type: (int) -> List[str]
        return self.git(
            "ls-tree",
            "-r",
            "--full-tree",
            "--name-only",
            "HEAD"
        ).strip().splitlines()


class gs_log_graph_toggle_all_setting(TextCommand, GitCommand):
    def run(self, edit):
        settings = self.view.settings()
        overview = settings.get("git_savvy.log_graph_view.overview")
        setting_name = "show_tags" if overview else "all_branches"
        current = settings.get("git_savvy.log_graph_view.{}".format(setting_name))
        next_state = not current
        settings.set("git_savvy.log_graph_view.{}".format(setting_name), next_state)
        self.view.run_command("gs_log_graph_refresh")


class gs_log_graph_open_commit(TextCommand):
    def run(self, edit):
        # type: (...) -> None
        window = self.view.window()
        if not window:
            return

        sel = get_simple_selection(self.view)
        if sel is None:
            return
        line = line_from_pt(self.view, sel)
        commit_hash = extract_commit_hash(line.text)
        if not commit_hash:
            return

        window.run_command("gs_show_commit", {"commit_hash": commit_hash})


PANEL_JUST_LOST_FOCUS = False


class GsLogGraphCursorListener(EventListener, GitCommand):
    def is_applicable(self, view):
        # type: (sublime.View) -> bool
        return bool(view.settings().get("git_savvy.log_graph_view"))

    def on_deactivated(self, view):
        # type: (sublime.View) -> None
        global PANEL_JUST_LOST_FOCUS
        window = view.window()
        if not window:
            return
        panel_view = window.find_output_panel('show_commit_info')
        PANEL_JUST_LOST_FOCUS = bool(panel_view and panel_view.id() == view.id())

    def on_activated(self, view):
        # type: (sublime.View) -> None
        window = view.window()
        if not window:
            return

        if view not in window.views():
            return

        if self.is_applicable(view):
            show_commit_info.ensure_panel(window)

        panel_view = window.find_output_panel('show_commit_info')
        if not panel_view:
            return

        # Do nothing, if the user focuses the panel
        if panel_view.id() == view.id():
            return

        # Auto-hide panel if the user switches to a different buffer
        if (
            not self.is_applicable(view)
            and show_commit_info.panel_is_visible(window)
            and show_commit_info.panel_belongs_to_graph(panel_view)
        ):
            if PANEL_JUST_LOST_FOCUS:
                panel_view.settings().set("git_savvy.show_commit_view.had_focus", True)

            panel = PREVIOUS_OPEN_PANEL_PER_WINDOW.get(window.id(), None)
            if panel:
                window.run_command("show_panel", {"panel": panel})
            else:
                window.run_command("hide_panel")

        # Auto-show panel if the user switches back
        elif (
            self.is_applicable(view)
            and not show_commit_info.panel_is_visible(window)
            and view.settings().get("git_savvy.log_graph_view.show_commit_info_panel")
        ):
            window.run_command("show_panel", {"panel": "output.show_commit_info"})
            if panel_view.settings().get("git_savvy.show_commit_view.had_focus"):
                # At this point `active_panel()` is already "show_commit_info"`
                # but still it can't receive focus before the next tick.
                # :shrug: as I could not find a reason or work-around.
                enqueue_on_ui(window.focus_view, panel_view)
                panel_view.settings().set("git_savvy.show_commit_view.had_focus", False)

    # `on_selection_modified` triggers twice per mouse click
    # multiplied with the number of views into the same buffer,
    # hence it is *important* to throttle these events.
    # We do this separately per side-effect. See the fn
    # implementations.
    def on_selection_modified(self, view):
        # type: (sublime.View) -> None
        if not self.is_applicable(view):
            return

        window = view.window()
        if window and show_commit_info.panel_is_visible(window):
            draw_info_panel(view)

        # `colorize_dots` queries the view heavily. We want that to
        # happen on the main thread (t.i. blocking) bc it is way, way
        # faster. But we still defer that task, so others can run code
        # that actually *needs* to be a sync side-effect to this event.
        enqueue_on_ui(colorize_dots, view)
        enqueue_on_ui(colorize_fixups, view)

        enqueue_on_ui(set_symbol_to_follow, view)

    def on_window_command(self, window, command_name, args):
        # type: (sublime.Window, str, Dict) -> None
        if command_name == 'hide_panel':
            view = window.active_view()
            if not view:
                return

            if window.active_panel() == "incremental_find":
                return

            # If the user hides the panel via `<ESC>` or mouse click,
            # remember the intent *only if* the `active_view` is a 'log_graph'
            if self.is_applicable(view):
                remember_commit_panel_state(view, False)
            PREVIOUS_OPEN_PANEL_PER_WINDOW[window.id()] = None

        elif command_name == 'show_panel':
            view = window.active_view()
            if not view:
                return

            # Special case some panels. For these panels, showing them does not count
            # as intent to close the show_commit panel. It will thus reappear
            # automatically as soon as you focus the graph again. E.g. closing the
            # incremental find panel via `<enter>` will bring the commit panel up
            # again.
            if args.get('panel') == "incremental_find":
                return

            toggle = args.get('toggle', False)
            panel = args.get('panel')
            if toggle and window.active_panel() == panel:  # <== actually *hide* panel
                # E.g. the same side-effect as in above "hide_panel" case
                if self.is_applicable(view):
                    remember_commit_panel_state(view, False)
                PREVIOUS_OPEN_PANEL_PER_WINDOW[window.id()] = None
            else:
                if panel == "output.show_commit_info":
                    if self.is_applicable(view):
                        remember_commit_panel_state(view, True)
                    PREVIOUS_OPEN_PANEL_PER_WINDOW[window.id()] = window.active_panel()
                    draw_info_panel(view)
                else:
                    if self.is_applicable(view):
                        remember_commit_panel_state(view, False)


PREVIOUS_OPEN_PANEL_PER_WINDOW = {}  # type: Dict[sublime.WindowId, Optional[str]]


def remember_commit_panel_state(view, state):
    # type: (sublime.View, bool) -> None
    # Note `view` is the ("parent") log graph view!
    view.settings().set("git_savvy.log_graph_view.show_commit_info_panel", state)
    # Also save to global state as the new initial mode
    # for the next graph view.
    GitSavvySettings().set("graph_show_more_commit_info", state)


def set_symbol_to_follow(view):
    # type: (sublime.View) -> None

    # `on_selection_modified` is called *often* while we render, just as
    # its by-product and without any intent of the user.
    # This is especially problematic if the cursor is on a line we don't
    # `follow` as we might overwrite `follow` and call `gs_log_graph_refresh`
    # again.
    # Filter early by checking the current `line.text`.  This is obviously
    # also okay and efficient when the user moves the cursor just left and right.
    # Note that we don't block *all* events here during "render" as that would
    # filter out intentional changes as well.  But we want that an intentional
    # move aborts the current render and restarts.  This is an important feature
    # for very long graphs.

    try:
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return

    line = line_from_pt(view, cursor)
    _set_symbol_to_follow(view, line.text)


@lru_cache(1)
def _set_symbol_to_follow(view: sublime.View, line_text: str) -> None:
    symbol = _extract_symbol_to_follow(view, line_text)
    if not symbol:
        return
    previous_value = view.settings().get('git_savvy.log_graph_view.follow')
    if symbol != previous_value:
        view.settings().set('git_savvy.log_graph_view.follow', symbol)

        # Check if the view endswith our `continuation_marker` and decide if we
        # need to expand or shrink the graph.
        continuation_marker = "...\n"
        view_size = view.size()
        continuation_line = sublime.Region(view_size - len(continuation_marker), view_size)
        if view.substr(continuation_line) == continuation_marker:
            try:
                cursor = [s.b for s in view.sel()][-1]
            except IndexError:
                return

            max_row, _ = view.rowcol(continuation_line.a)
            cur_row, _ = view.rowcol(cursor)
            if not (GRAPH_HEIGHT * 0.5 < max_row - cur_row < GRAPH_HEIGHT * 2):
                view.run_command("gs_log_graph_refresh")


def extract_symbol_to_follow(view):
    # type: (sublime.View) -> Optional[str]
    """Extract a symbol to follow."""
    try:
        # Intentional `b` (not `end()`!) because b is where the
        # cursor is. (If you select upwards b becomes < a.)
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return None

    line = line_from_pt(view, cursor)
    return _extract_symbol_to_follow(view, line.text)


@lru_cache(maxsize=512)
def _extract_symbol_to_follow(view, line_text):
    # type: (sublime.View, str) -> Optional[str]
    try:
        # Intentional `b` (not `end()`!) because b is where the
        # cursor is. (If you select upwards b becomes < a.)
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return None

    if view.match_selector(cursor, 'meta.graph.graph-line.head.git-savvy'):
        return 'HEAD'

    line_span = view.line(cursor)
    symbols_on_line = [
        symbol
        for r, symbol in view.symbols()
        if line_span.contains(r)
    ]
    if symbols_on_line:
        # git always puts the remotes first so we take
        # the last one which is (then) a local branch.
        return symbols_on_line[-1]

    return extract_commit_hash(line_text)


def navigate_to_symbol(
    view,            # type: sublime.View
    symbol,          # type: str
    if_before=None,  # type: sublime.Region
    col_range=None,  # type: Tuple[int, int]
    method=None,     # type: Callable[[sublime.View, Union[sublime.Region, sublime.Point]], None]
):
    # type: (...) -> bool
    jump_position = jump_position_for_symbol(view, symbol, if_before, col_range)
    if jump_position is None:
        return False

    if method is None:
        method = set_and_show_cursor
    method(view, jump_position)
    return True


def jump_position_for_symbol(
    view,            # type: sublime.View
    symbol,          # type: str
    if_before=None,  # type: Optional[sublime.Region]
    col_range=None   # type: Optional[Tuple[int, int]]
):
    # type: (...) -> Optional[Union[sublime.Region, sublime.Point]]
    region = _find_symbol(view, symbol)
    if region is None:  # explicit `None` checks bc empty regions are falsy!
        return None
    if if_before is not None and region.end() > if_before.end():
        return None

    if col_range is None:
        return region.begin()

    line_start = line_start_of_region(view, region)
    wanted_region = Region(*col_range) + line_start
    # Normalize single cursors *before* the commit hash
    # (t.i. `region.end()`) to `region.begin()`.
    if wanted_region.empty() and wanted_region.a < region.end():
        return region.begin()
    else:
        return wanted_region


def _find_symbol(view, symbol):
    # type: (sublime.View, str) -> Optional[sublime.Region]
    if symbol == 'HEAD':
        try:
            return view.find_by_selector(
                'meta.graph.graph-line.head.git-savvy '
                'constant.numeric.graph.commit-hash.git-savvy'
            )[0]
        except IndexError:
            return None

    for r, symbol_ in view.symbols():
        if symbol_ == symbol:
            line = line_from_pt(view, r)
            return extract_comit_hash_span(view, line)

    r = view.find(FIND_COMMIT_HASH + re.escape(symbol), 0)
    if not r.empty():
        line = line_from_pt(view, r)
        return extract_comit_hash_span(view, line)
    return None


def extract_comit_hash_span(view, line):
    # type: (sublime.View, TextRange) -> Optional[sublime.Region]
    match = COMMIT_LINE.search(line.text)
    if match:
        a, b = match.span('commit_hash')
        return sublime.Region(a + line.a, b + line.a)
    return None


FIND_COMMIT_HASH = "^[{graph_chars}]*[{node_chars}][{graph_chars}]* ".format(
    graph_chars=GRAPH_CHAR_OPTIONS, node_chars=colorizer.COMMIT_NODE_CHARS
)


@text_command
def set_and_show_cursor(view, point_or_region):
    # type: (sublime.View, Union[sublime.Region, sublime.Point]) -> None
    just_set_cursor(view, point_or_region)
    view.show(view.sel(), True)


@text_command
def just_set_cursor(view, point_or_region):
    # type: (sublime.View, Union[sublime.Region, sublime.Point]) -> None
    sel = view.sel()
    sel.clear()
    sel.add(point_or_region)


def get_simple_selection(view):
    # type: (sublime.View) -> Optional[sublime.Region]
    sel = [s for s in view.sel()]
    if len(sel) != 1 or len(view.lines(sel[0])) != 1:
        return None

    return sel[0]


def get_column_range(view, region):
    # type: (sublime.View, sublime.Region) -> Tuple[int, int]
    line_start = line_start_of_region(view, region)
    return (region.begin() - line_start, region.end() - line_start)


def is_sel_in_viewport(view):
    # type: (sublime.View) -> bool
    viewport = view.visible_region()
    return all(viewport.contains(s) or viewport.intersects(s) for s in view.sel())


def line_start_of_region(view, region):
    # type: (sublime.View, sublime.Region) -> sublime.Point
    return view.line(region).begin()


def colorize_dots(view):
    # type: (sublime.View) -> None
    dots = find_graph_art_at_cursor(view) or tuple(find_dots(view))
    _colorize_dots(view.id(), dots)


def find_graph_art_at_cursor(view):
    # type: (sublime.View) -> Tuple[colorizer.Char, ...]
    if len(view.sel()) != 1:
        return ()
    cursor = view.sel()[0].b
    if not view.match_selector(cursor, "meta.graph.branch-art"):
        return ()

    c = colorizer.Char(view, cursor)
    for get_char in (lambda: c, lambda: c.w):
        c_ = get_char()
        if c_ != " ":
            return (c_,)
    return ()


def find_dots(view):
    # type: (sublime.View) -> Set[colorizer.Char]
    return set(_find_dots(view))


def _find_dots(view):
    # type: (sublime.View) -> Iterator[colorizer.Char]
    for s in view.sel():
        line = line_from_pt(view, s.begin())
        dot = dot_from_line(view, line)
        if dot:
            yield dot


def line_from_pt(view, pt):
    # type: (sublime.View, Union[sublime.Point, sublime.Region]) -> TextRange
    line_span = view.line(pt)
    line_text = view.substr(line_span)
    return TextRange(line_text, line_span.a, line_span.b)


def dot_from_line(view, line):
    # type: (sublime.View, TextRange) -> Optional[colorizer.Char]
    for ch in colorizer.COMMIT_NODE_CHARS:
        idx = line.text.find(ch)
        if idx > -1:
            return colorizer.Char(view, line.region().begin() + idx)
    return None


ACTIVE_COMPUTATION = Cache()


@lru_cache(maxsize=1)
# ^- throttle side-effects
def _colorize_dots(vid, dots):
    # type: (sublime.ViewId, Tuple[colorizer.Char]) -> None
    view = sublime.View(vid)
    to_region = lambda ch: ch.region()  # type: Callable[[colorizer.Char], sublime.Region]

    view.add_regions(
        'gs_log_graph.dot',
        list(map(to_region, dots)),
        scope=DOT_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )

    ACTIVE_COMPUTATION[vid] = dots
    __colorize_dots(vid, dots)


@cooperative_thread_hopper
def __colorize_dots(vid, dots):
    # type: (sublime.ViewId, Tuple[colorizer.Char]) -> HopperR
    timer = yield "ENSURE_UI_THREAD"
    view = sublime.View(vid)

    paths_down = []  # type: List[List[colorizer.Char]]
    paths_up = []  # type: List[List[colorizer.Char]]

    uow = []
    for container, direction in ((paths_down, "down"), (paths_up, "up")):
        for dot in dots:
            try:
                chars = colorizer.follow_path_if_cached(dot, direction)  # type: ignore[arg-type]
            except ValueError:
                values = []  # type: List[colorizer.Char]
                container.append(values)
                uow.append((colorizer.follow_path(dot, direction), values))  # type: ignore[arg-type]
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


def __paint(view, paths_down, paths_up):
    # type: (sublime.View, List[List[colorizer.Char]], List[List[colorizer.Char]]) -> None
    to_region = lambda ch: ch.region()  # type: Callable[[colorizer.Char], sublime.Region]
    path_down = flatten(filter(
        lambda path: len(path) > 1,  # type: ignore[arg-type]  # https://github.com/python/mypy/issues/9176
        paths_down
    ))
    chars_up = flatten(filter(
        lambda path: len(path) > 1,  # type: ignore[arg-type]  # https://github.com/python/mypy/issues/9176
        paths_up
    ))
    path_up, dot_up = partition(lambda ch: ch.char() in colorizer.COMMIT_NODE_CHARS, chars_up)
    view.add_regions(
        'gs_log_graph.path_below',
        list(map(to_region, path_down)),
        scope=PATH_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )
    view.add_regions(
        'gs_log_graph.path_above',
        list(map(to_region, path_up)),
        scope=PATH_ABOVE_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )
    view.add_regions(
        'gs_log_graph.dot.above',
        list(map(to_region, dot_up)),
        scope=DOT_ABOVE_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )


def colorize_fixups(view):
    # type: (sublime.View) -> None
    dots = tuple(find_dots(view))
    _colorize_fixups(view.id(), dots)


@lru_cache(maxsize=1)
def _colorize_fixups(vid, dots):
    # type: (sublime.ViewId, Tuple[colorizer.Char]) -> None
    view = sublime.View(vid)

    matching_dots = flatten(__find_matching_dots(vid, dot) for dot in dots)
    view.add_regions(
        'gs_log_graph_follow_fixups',
        [dot.region() for dot in matching_dots],
        scope=MATCHING_COMMIT_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )


@lru_cache(maxsize=64)  # If we cache, we must return non-lazy `List`!
def __find_matching_dots(vid, dot):
    # type: (sublime.ViewId, colorizer.Char) -> List[colorizer.Char]
    view = sublime.View(vid)
    commit_message = commit_message_from_point(view, dot.pt)
    if not commit_message:
        return []

    if is_fixup_or_squash_message(commit_message):
        original_message = strip_fixup_or_squash_prefix(commit_message)
        return take(1, find_matching_commit(dot, original_message))
    else:
        return list(dot for dot, _ in find_fixups_upwards(dot, commit_message))


def extract_message_regions(view):
    # type: (sublime.View) -> List[sublime.Region]
    return find_by_selector(view, "meta.graph.message.git-savvy")


def is_fixup_or_squash_message(commit_message):
    # type: (str) -> bool
    return (
        commit_message.startswith("fixup! ")
        or commit_message.startswith("squash! ")
    )


def strip_fixup_or_squash_prefix(commit_message):
    # type: (str) -> str
    # As long as we process "visually", we must deal with
    # truncated messages which end with one or multiple dots
    # we have to strip.
    if commit_message.startswith('fixup! '):
        return commit_message[7:].rstrip('.').strip()
    if commit_message.startswith('squash! '):
        return commit_message[8:].rstrip('.').strip()
    return commit_message


def add_fixup_or_squash_prefixes(commit_message):
    # type: (str) -> List[str]
    return [
        "fixup! " + commit_message,
        "squash! " + commit_message
    ]


def commit_message_from_point(view, pt):
    # type: (sublime.View, int) -> Optional[str]
    line_span = view.line(pt)
    for r in extract_message_regions(view):
        if line_span.a <= r.a <= line_span.b:  # optimized `line_span.contains(r)`
            return view.substr(r)
    else:
        return None


def find_matching_commit(dot, message, forward=True):
    # type: (colorizer.Char, str, bool) -> Iterator[colorizer.Char]
    dots = follow_dots(dot, forward=forward)
    for dot, this_message in _with_message(take(100, dots)):
        this_message = this_message.rstrip(".").strip()
        shorter, longer = sorted((message, this_message), key=len)
        if longer.startswith(shorter):
            yield dot


def find_fixups_upwards(dot, message):
    # type: (colorizer.Char, str) -> Iterator[Tuple[colorizer.Char, str]]
    messages = add_fixup_or_squash_prefixes(message.rstrip(".").strip())

    previous_dots = follow_dots(dot, forward=False)
    for dot, this_message in _with_message(take(50, previous_dots)):
        this_message = this_message.rstrip(".").strip()
        if is_fixup_or_squash_message(this_message):
            for message in messages:
                shorter, longer = sorted((message, this_message), key=len)
                if longer.startswith(shorter):
                    yield dot, this_message


def _with_message(dots):
    # type: (Iterable[colorizer.Char]) -> Iterator[Tuple[colorizer.Char, str]]
    return filter_(map(with_message, dots))


def with_message(dot):
    # type: (colorizer.Char) -> Optional[Tuple[colorizer.Char, str]]
    commit_message = commit_message_from_point(dot.view, dot.pt)
    if commit_message:
        return dot, commit_message
    return None


def draw_info_panel(view):
    # type: (sublime.View) -> None
    """Extract line under the last cursor and draw info panel."""
    try:
        # Intentional `b` (not `end()`!) because b is where the
        # cursor is. (If you select upwards b becomes < a.)
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return

    line = line_from_pt(view, cursor)
    draw_info_panel_for_line(view.id(), line.text)


@lru_cache(maxsize=1)
# ^- used to throttle the side-effect!
# Read: distinct until      (vid, line_text) changes
def draw_info_panel_for_line(vid, line_text):
    # type: (sublime.ViewId, str) -> None
    view = sublime.View(vid)
    window = view.window()
    if not window:
        return

    commit_hash = extract_commit_hash(line_text)
    # `gs_show_commit_info` draws a blank panel if `commit_hash`
    # is falsy.  That only looks nice iff the main graph view is
    # also blank. (Which it only ever is directly after creation.)
    # If you just move the cursor to a line not containing a
    # commit_hash, it looks better to not draw at all, t.i. the
    # information in the panel stays untouched.
    if view.size() == 0 or commit_hash:
        window.run_command("gs_show_commit_info", {
            "commit_hash": commit_hash,
            "from_log_graph": True
        })


def extract_commit_hash(line):
    # type: (str) -> str
    match = COMMIT_LINE.search(line)
    return match.group('commit_hash') if match else ""


class gs_log_graph_toggle_commit_info_panel(WindowCommand, GitCommand):
    """ Toggle commit info output panel."""
    def run(self):
        if show_commit_info.panel_is_visible(self.window):
            self.window.run_command("hide_panel", {"panel": "output.show_commit_info"})
        else:
            self.window.run_command("show_panel", {"panel": "output.show_commit_info"})


class gs_log_graph_show_and_focus_panel(WindowCommand, GitCommand):
    def run(self, panel: str) -> None:
        if self.window.active_panel() != f"output.{panel}":
            self.window.run_command("show_panel", {"panel": f"output.{panel}"})

        panel_view = self.window.find_output_panel(panel)
        if not panel_view:
            return

        self.window.focus_view(panel_view)


class LineInfo(TypedDict, total=False):
    commit: str
    HEAD: str
    branches: List[str]
    local_branches: List[str]
    tags: List[str]


ListItems = Literal["branches", "local_branches", "tags"]


def describe_graph_line(line, known_branches):
    # type: (str, Dict[str, Branch]) -> Optional[LineInfo]
    match = COMMIT_LINE.match(line)
    if match is None:
        return None

    commit_hash = match.group("commit_hash")
    decoration = match.group("decoration")

    rv = {"commit": commit_hash}  # type: LineInfo
    if decoration:
        names = decoration.split(", ")
        if names[0].startswith("HEAD"):
            head, *names = names
            if head == "HEAD" or head == "HEAD*":
                rv["HEAD"] = commit_hash
            else:
                branch = head[head.index("-> ") + 3:]
                rv["HEAD"] = branch
                names = [branch] + names
        branches, local_branches, tags = [], [], []
        for name in names:
            if name.startswith("tag: "):
                tags.append(name[len("tag: "):])
            else:
                branches.append(name)
                branch = known_branches.get(name)
                if branch and branch.is_local:
                    local_branches.append(name)
        if branches:
            rv["branches"] = branches
        if local_branches:
            rv["local_branches"] = local_branches
        if tags:
            rv["tags"] = tags

    return rv


def describe_head(view, branches):
    # type: (sublime.View, Dict[str, Branch]) -> Optional[LineInfo]
    try:
        region = view.find_by_selector(
            'meta.graph.graph-line.head.git-savvy '
            'constant.numeric.graph.commit-hash.git-savvy'
        )[0]
    except IndexError:
        return None

    cursor = region.b
    line = line_from_pt(view, cursor)
    return describe_graph_line(line.text, branches)


def format_revision_list(revisions):
    # type: (Sequence[str]) -> str
    return (
        "{}".format(*revisions)
        if len(revisions) == 1
        else "{} and {}".format(*revisions)
        if len(revisions) == 2
        else "{}, {}, and {}".format(revisions[0], revisions[1], revisions[-1])
        if len(revisions) == 3
        else "{}, {} ... {}".format(revisions[0], revisions[1], revisions[-1])
    )


class gs_log_graph_action(WindowCommand, GitCommand):
    selected_index = 0

    def run(self):
        view = self.window.active_view()
        if not view:
            return

        branches = {b.canonical_name: b for b in self.get_branches()}
        infos = list(filter_(
            describe_graph_line(line, branches)
            for line in unique(
                view.substr(line)
                for s in view.sel()
                for line in view.lines(s)
            )
        ))
        if not infos:
            return

        actions = (
            self.actions_for_single_line(view, infos[0], branches)
            if len(infos) == 1
            else self.actions_for_multiple_lines(view, infos)
        )
        if not actions:
            return

        def on_action_selection(index):
            if index == -1:
                return

            self.selected_index = index
            description, action = actions[index]
            action()

        selected_index = self.selected_index
        if 0 <= selected_index < len(actions) - 1:
            selected_action = actions[selected_index]
            if selected_action == SEPARATOR:
                selected_index += 1

        self.window.show_quick_panel(
            [a[0] for a in actions],
            on_action_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=selected_index,
        )

    def _get_file_path(self, view):
        # type: (sublime.View) -> Optional[str]
        settings = view.settings()
        apply_filters = settings.get("git_savvy.log_graph_view.apply_filters")
        paths = (
            settings.get("git_savvy.log_graph_view.paths", [])
            if apply_filters
            else []
        )  # type: List[str]
        if len(paths) == 1:
            return os.path.normcase(os.path.join(self.repo_path, paths[0]))

        return None

    def actions_for_multiple_lines(self, view, infos):
        # type: (sublime.View, List[LineInfo]) -> List[Tuple[str, Callable[[], None]]]
        file_path = self._get_file_path(view)
        actions = []  # type: List[Tuple[str, Callable[[], None]]]

        if len(infos) == 2:
            def display_name(info):
                # type: (LineInfo) -> str
                if info.get("local_branches"):
                    return info["local_branches"][0]
                branches = info.get("branches", [])
                if len(branches) == 1:
                    return branches[0]
                elif len(branches) == 0 and info.get("tags"):
                    return info["tags"][0]
                else:
                    return info["commit"]

            base_commit = display_name(infos[1])
            target_commit = display_name(infos[0])

            actions += [
                (
                    "Diff {}{}...{}".format(
                        "file " if file_path else "", base_commit, target_commit
                    ),
                    partial(self.sym_diff_two_commits, base_commit, target_commit, file_path)
                ),
                (
                    "Diff {}{}..{}".format(
                        "file " if file_path else "", base_commit, target_commit
                    ),
                    partial(self.diff_commit, base_commit, target_commit, file_path)
                ),
                (
                    "Compare {}'{}' and '{}'".format(
                        "file between " if file_path else "", base_commit, target_commit
                    ),
                    partial(self.compare_commits, base_commit, target_commit, file_path)
                ),
                (
                    "Show file history from {}..{}".format(base_commit, target_commit)
                    if file_path
                    else "Show graph for {}..{}".format(base_commit, target_commit),
                    partial(self.graph_two_revisions, base_commit, target_commit, file_path)
                ),
                (
                    "Show file history from {}..{}".format(target_commit, base_commit)
                    if file_path
                    else "Show graph for {}..{}".format(target_commit, base_commit),
                    partial(self.graph_two_revisions, target_commit, base_commit, file_path)
                )

            ]

        pickable = list(reversed([
            info["commit"]
            for info in infos
            if "HEAD" not in info
        ]))
        if pickable:
            actions += [
                (
                    "Cherry-pick {}".format(format_revision_list(pickable)),
                    partial(self.cherry_pick, *pickable)
                )
            ]

        revertable = list(reversed([info["commit"] for info in infos]))
        actions += [
            (
                "Revert {}".format(format_revision_list(revertable)),
                partial(self.revert_commit, *revertable)
            )
        ]

        return actions

    def sym_diff_two_commits(self, base_commit, target_commit, file_path=None):
        self.window.run_command("gs_diff", {
            "in_cached_mode": False,
            "file_path": file_path,
            "base_commit": "{}...{}".format(base_commit, target_commit),
            "disable_stage": True
        })

    def graph_two_revisions(self, base_commit, target_commit, file_path=None):
        branches = ["{}..{}".format(base_commit, target_commit)]
        self.window.run_command("gs_graph", {
            'all': False,
            'file_path': file_path,
            'branches': branches,
            'follow': base_commit
        })

    def actions_for_single_line(self, view, info, branches):
        # type: (sublime.View, LineInfo, Dict[str, Branch]) -> List[Tuple[str, Callable[[], None]]]
        commit_hash = info["commit"]
        file_path = self._get_file_path(view)
        actions = []  # type: List[Tuple[str, Callable[[], None]]]
        on_checked_out_branch = "HEAD" in info and info["HEAD"] in info.get("local_branches", [])
        if on_checked_out_branch:
            current_branch = info["HEAD"]
            b = branches[current_branch]
            if b.upstream:
                actions += [
                    ("Fetch", partial(self.fetch, current_branch)),
                    ("Pull", self.pull),
                ]
            actions += [
                ("Push", partial(self.push, current_branch)),
                ("Rebase on...", partial(self.rebase_on)),
                SEPARATOR,
            ]

        actions += [
            ("Checkout '{}'".format(branch_name), partial(self.checkout, branch_name))
            for branch_name in info.get("local_branches", [])
            if info.get("HEAD") != branch_name
        ]

        good_commit_name = (
            info["tags"][0]
            if info.get("tags")
            else commit_hash
        )
        if "HEAD" not in info or info["HEAD"] != commit_hash:
            actions += [
                (
                    "Checkout '{}' detached".format(good_commit_name),
                    partial(self.checkout, good_commit_name)
                ),
            ]

        for branch_name in info.get("local_branches", []):
            if branch_name == info.get("HEAD"):
                continue

            b = branches[branch_name]
            if b.upstream and b.upstream.status != "gone":
                if "behind" in b.upstream.status and "ahead" not in b.upstream.status:
                    actions += [
                        (
                            "Fast-forward '{}' to '{}'".format(branch_name, b.upstream.canonical_name),
                            partial(self.move_branch, branch_name, b.upstream.canonical_name)
                        ),
                    ]
                else:
                    actions += [
                        (
                            "Update '{}' from '{}'".format(branch_name, b.upstream.canonical_name),
                            partial(self.update_from_tracking, b.upstream.remote, b.upstream.branch, b.name)
                        ),
                    ]

                actions += [
                    (
                        "Push '{}' to '{}'".format(branch_name, b.upstream.canonical_name),
                        partial(self.push, branch_name)
                    ),
                ]
            else:
                actions += [
                    (
                        "Push '{}'".format(branch_name),
                        partial(self.push, branch_name)
                    ),
                ]

        if file_path:
            actions += [
                ("Show file at commit", partial(self.show_file_at_commit, commit_hash, file_path)),
                ("Blame file at commit", partial(self.blame_file_atcommit, commit_hash, file_path)),
                (
                    "Checkout file at commit",
                    partial(self.checkout_file_at_commit, commit_hash, file_path)
                )
            ]

        actions += [
            (
                "Create branch at '{}'".format(good_commit_name),
                partial(self.create_branch, commit_hash)
            ),
            (
                "Create tag at '{}'".format(commit_hash),
                partial(self.create_tag, commit_hash)
            )
        ]
        actions += [
            ("Delete tag '{}'".format(tag_name), partial(self.delete_tag, tag_name))
            for tag_name in info.get("tags", [])
        ]

        head_info = describe_head(view, branches)
        head_is_on_a_branch = head_info and head_info["HEAD"] != head_info["commit"]
        cursor_is_not_on_head = head_info and head_info["commit"] != info["commit"]

        def get_list(info, key):
            # type: (LineInfo, ListItems) -> List[str]
            return info.get(key, [])

        if head_info and head_is_on_a_branch and cursor_is_not_on_head:
            get = partial(get_list, info)  # type: Callable[[ListItems], List[str]]
            good_move_target = next(
                chain(get("local_branches"), get("branches")),
                good_commit_name
            )
            actions += [
                (
                    "Move '{}' to '{}'".format(head_info["HEAD"], good_move_target),
                    partial(self.checkout_b, head_info["HEAD"], good_commit_name)
                ),
            ]

        if not head_info or cursor_is_not_on_head:
            good_head_name = (
                "'{}'".format(head_info["HEAD"])  # type: ignore
                if head_is_on_a_branch
                else "HEAD"
            )
            get = partial(get_list, info)  # type: Callable[[ListItems], List[str]]  # type: ignore[no-redef]
            good_reset_target = next(
                chain(get("local_branches"), get("branches")),
                good_commit_name
            )
            actions += [
                (
                    "Reset {} to '{}'".format(good_head_name, good_reset_target),
                    partial(self.reset_to, good_reset_target)
                )
            ]

        if head_info and not head_is_on_a_branch and cursor_is_not_on_head:
            get = partial(get_list, head_info)  # type: Callable[[ListItems], List[str]]  # type: ignore[no-redef]
            good_move_target = next(
                (
                    "'{}'".format(name)
                    for name in chain(get("local_branches"), get("branches"), get("tags"))
                ),
                "HEAD"
            )
            actions += [
                (
                    "Move '{}' to {}".format(branch_name, good_move_target),
                    partial(self.checkout_b, branch_name)
                )
                for branch_name in info.get("local_branches", [])
            ]

        actions += [
            ("Delete branch '{}'".format(branch_name), partial(self.delete_branch, branch_name))
            for branch_name in info.get("local_branches", [])
        ]

        if "HEAD" not in info:
            actions += [
                ("Cherry-pick commit", partial(self.cherry_pick, commit_hash)),
            ]

        actions += [
            ("Revert commit", partial(self.revert_commit, commit_hash)),
        ]
        if not head_info or cursor_is_not_on_head:
            good_head_name = (
                "'{}'".format(head_info["HEAD"])  # type: ignore[index]
                if head_is_on_a_branch
                else "HEAD"
            )
            get = partial(get_list, info)  # type: Callable[[ListItems], List[str]]  # type: ignore[no-redef]
            good_target_name = next(
                chain(get("local_branches"), get("branches")),
                good_commit_name
            )
            actions += [
                (
                    "Compare '{}' with {}".format(good_target_name, good_head_name),
                    partial(
                        self.compare_commits,
                        head_info["HEAD"] if head_is_on_a_branch else commit_hash,  # type: ignore[index]
                        good_target_name,
                        file_path=file_path,
                    )
                )
            ]
        else:
            interesting_candidates = branches.keys() & ["main", "master", "dev"]
            target_hints = sorted(interesting_candidates, key=lambda branch: -branches[branch].committerdate)
            actions += [
                (
                    "Compare {}against ...".format("file " if file_path else ""),
                    partial(
                        self.compare_against,
                        info["HEAD"] if on_checked_out_branch else commit_hash,
                        file_path=file_path,
                        target_hints=target_hints
                    )
                ),
            ]

        if file_path:
            actions += [
                (
                    "Diff file against workdir",
                    partial(self.diff_commit, commit_hash, file_path=file_path)
                ),
            ]
        elif "HEAD" in info:
            actions += [
                ("Diff against workdir", self.diff),
            ]
        else:
            actions += [
                (
                    "Diff '{}' against HEAD".format(good_commit_name),
                    partial(self.diff_commit, commit_hash, target_commit="HEAD")
                ),
            ]
        return actions

    def pull(self):  # type: ignore[override]
        self.window.run_command("gs_pull")

    def push(self, current_branch):  # type: ignore[override]
        self.window.run_command("gs_push", {"local_branch_name": current_branch})

    def fetch(self, current_branch):  # type: ignore[override]
        remote = self.get_remote_for_branch(current_branch)
        self.window.run_command("gs_fetch", {"remote": remote} if remote else None)

    def rebase_on(self):
        self.window.run_command("gs_rebase_on_branch")

    def update_from_tracking(self, remote, remote_name, local_name):
        # type: (str, str, str) -> None
        self.window.run_command("gs_fetch", {
            "remote": remote,
            "refspec": "{}:{}".format(remote_name, local_name)
        })

    def checkout(self, commit_hash):
        self.window.run_command("gs_checkout_branch", {"branch": commit_hash})

    def checkout_b(self, branch_name, start_point=None):
        self.window.run_command("gs_checkout_new_branch", {
            "branch_name": branch_name,
            "start_point": start_point,
            "force": True,
        })

    def move_branch(self, branch_name, target):
        self.git("branch", "-f", branch_name, target)
        util.view.refresh_gitsavvy_interfaces(self.window)

    def delete_branch(self, branch_name):
        self.window.run_command("gs_delete_branch", {"branch": branch_name})

    def show_commit(self, commit_hash):
        self.window.run_command("gs_show_commit", {"commit_hash": commit_hash})

    def create_branch(self, commit_hash):
        self.window.run_command("gs_create_branch", {"start_point": commit_hash})

    def create_tag(self, commit_hash):
        self.window.run_command("gs_tag_create", {"target_commit": commit_hash})

    def delete_tag(self, tag_name):
        self.git("tag", "-d", tag_name)
        util.view.refresh_gitsavvy_interfaces(self.window)

    def reset_to(self, commitish):
        self.window.run_command("gs_reset", {"commit_hash": commitish})

    def cherry_pick(self, *commit_hash):
        try:
            self.git("cherry-pick", *commit_hash)
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def revert_commit(self, *commit_hash):
        self.window.run_command("gs_revert_commit", {"commit_hash": commit_hash})

    def compare_commits(self, base_commit, target_commit, file_path=None):
        self.window.run_command("gs_compare_commit", {
            "base_commit": base_commit,
            "target_commit": target_commit,
            "file_path": file_path
        })

    def compare_against(self, base_commit, file_path=None, target_hints=None):
        nearest_tag = self.git("describe", "--abbrev=0").strip()
        if nearest_tag:
            if target_hints is None:
                target_hints = []
            target_hints += [nearest_tag]
        self.window.run_command("gs_compare_against", {
            "base_commit": base_commit,
            "file_path": file_path,
            "target_hints": target_hints
        })

    def copy_sha(self, commit_hash):
        sublime.set_clipboard(self.git("rev-parse", commit_hash).strip())

    def diff(self):
        self.window.run_command("gs_diff", {"in_cached_mode": False})

    def diff_commit(self, base_commit, target_commit=None, file_path=None):
        self.window.run_command("gs_diff", {
            "in_cached_mode": False,
            "file_path": file_path,
            "base_commit": base_commit,
            "target_commit": target_commit,
            "disable_stage": True
        })

    def show_file_at_commit(self, commit_hash, file_path):
        self.window.run_command("gs_show_file_at_commit", {
            "commit_hash": commit_hash,
            "filepath": file_path
        })

    def blame_file_atcommit(self, commit_hash, file_path):
        self.window.run_command("gs_blame", {
            "commit_hash": commit_hash,
            "file_path": file_path
        })

    def checkout_file_at_commit(self, commit_hash, file_path):
        self.checkout_ref(commit_hash, fpath=file_path)
        util.view.refresh_gitsavvy_interfaces(self.window)
