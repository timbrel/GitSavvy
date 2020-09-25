from collections import deque
from functools import lru_cache, partial
from itertools import chain, count, islice
import locale
import os
from queue import Empty
import re
import shlex
import subprocess
import time
import threading

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from . import log_graph_colorizer as colorizer, show_commit_info
from .log import GsLogCommand
from .. import utils
from ..fns import filter_, flatten, pairwise, partition, take, unique
from ..git_command import GitCommand, GitSavvyError
from ..parse_diff import Region
from ..settings import GitSavvySettings
from ..runtime import (
    enqueue_on_ui, enqueue_on_worker,
    run_or_timeout, run_on_new_thread,
    text_command
)
from ..view import join_regions, line_distance, replace_view_content, show_region
from ..ui_mixins.input_panel import show_single_line_input_panel
from ..ui_mixins.quick_panel import show_branch_panel
from ..utils import add_selection_to_jump_history, focus_view, show_toast
from ...common import util
from ...common.theme_generator import ThemeGenerator


__all__ = (
    "gs_graph",
    "gs_graph_current_file",
    "gs_log_graph_refresh",
    "gs_log_graph",
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
    "gs_log_graph_toggle_all_setting",
    "gs_log_graph_open_commit",
    "gs_log_graph_toggle_more_info",
    "gs_log_graph_action",
    "GsLogGraphCursorListener",
)

MYPY = False
if MYPY:
    from typing import (
        Callable, Dict, Generic, Iterable, Iterator, List, Optional, Set, Sequence, Tuple,
        TypeVar, Union
    )
    T = TypeVar('T')


COMMIT_NODE_CHAR = "●"
COMMIT_NODE_CHAR_OPTIONS = "●*"
GRAPH_CHAR_OPTIONS = r" /_\|\-\\."
COMMIT_LINE = re.compile(
    r"^[{graph_chars}]*[{node_chars}][{graph_chars}]* "
    r"(?P<commit_hash>[a-f0-9]{{5,40}}) "
    r"(?P<decoration>\(.+?\))?"
    .format(graph_chars=GRAPH_CHAR_OPTIONS, node_chars=COMMIT_NODE_CHAR_OPTIONS)
)

DOT_SCOPE = 'git_savvy.graph.dot'
DOT_ABOVE_SCOPE = 'git_savvy.graph.dot.above'
PATH_SCOPE = 'git_savvy.graph.path_char'
PATH_ABOVE_SCOPE = 'git_savvy.graph.path_char.above'
MATCHING_COMMIT_SCOPE = 'git_savvy.graph.matching_commit'


def compute_identifier_for_view(view):
    # type: (sublime.View) -> Optional[Tuple]
    settings = view.settings()
    return (
        settings.get('git_savvy.repo_path'),
        settings.get('git_savvy.file_path'),
        settings.get('git_savvy.log_graph_view.all_branches')
        or settings.get('git_savvy.log_graph_view.branches'),
        settings.get('git_savvy.log_graph_view.filters')
    ) if settings.get('git_savvy.log_graph_view') else None


class gs_graph(WindowCommand, GitCommand):
    def run(
        self,
        repo_path=None,
        file_path=None,
        all=False,
        branches=None,
        author='',
        title='GRAPH',
        follow=None,
        decoration='sparse',
        filters=''
    ):
        if repo_path is None:
            repo_path = self.repo_path
        assert repo_path

        this_id = (
            repo_path,
            file_path,
            all or branches,
            filters
        )
        for view in self.window.views():
            if compute_identifier_for_view(view) == this_id:
                settings = view.settings()
                settings.set("git_savvy.log_graph_view.all_branches", all)
                settings.set("git_savvy.log_graph_view.filter_by_author", author)
                settings.set("git_savvy.log_graph_view.branches", branches or [])
                settings.set('git_savvy.log_graph_view.follow', follow)
                settings.set('git_savvy.log_graph_view.decoration', decoration)
                settings.set('git_savvy.log_graph_view.filters', filters)

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

                focus_view(view)
                break
        else:
            view = util.view.get_scratch_view(self, "log_graph", read_only=True)
            view.set_syntax_file("Packages/GitSavvy/syntax/graph.sublime-syntax")
            view.run_command("gs_handle_vintageous")
            view.run_command("gs_handle_arrow_keys")
            run_on_new_thread(augment_color_scheme, view)

            settings = view.settings()
            settings.set("git_savvy.repo_path", repo_path)
            settings.set("git_savvy.file_path", file_path)
            settings.set("git_savvy.log_graph_view.all_branches", all)
            settings.set("git_savvy.log_graph_view.filter_by_author", author)
            settings.set("git_savvy.log_graph_view.branches", branches or [])
            settings.set('git_savvy.log_graph_view.follow', follow)
            settings.set('git_savvy.log_graph_view.decoration', decoration)
            settings.set('git_savvy.log_graph_view.filters', filters)
            show_commit_info_panel = bool(self.savvy_settings.get("graph_show_more_commit_info"))
            settings.set(
                "git_savvy.log_graph_view.show_commit_info_panel",
                show_commit_info_panel
            )
            view.set_name(title)

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

            view.run_command("gs_log_graph_refresh", {"navigate_after_draw": True})


class gs_graph_current_file(WindowCommand, GitCommand):
    def run(self, **kwargs):
        file_path = self.file_path
        if file_path:
            self.window.run_command('gs_graph', dict(file_path=file_path, **kwargs))
        else:
            self.window.status_message("View has no filename to track.")


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


DATE_FORMAT = 'human'
FALLBACK_DATE_FORMAT = 'format:%Y-%m-%d %H:%M'
DATE_FORMAT_STATE = 'trying'


MYPY = False
if MYPY:
    from typing import NamedTuple
    Ins = NamedTuple('Ins', [('idx', int), ('line', str)])
    Del = NamedTuple('Del', [('start', int), ('end', int)])
    Replace = NamedTuple('Replace', [('start', int), ('end', int), ('text', List[str])])
else:
    from collections import namedtuple
    Ins = namedtuple('Ins', 'idx line')
    Del = namedtuple('Del', 'start end')
    Replace = namedtuple('Replace', 'start end text')


MAX_LOOK_AHEAD = 10000
if MYPY:
    from enum import Enum

    class FlushT(Enum):
        token = 0
    Flush = FlushT.token

else:
    Flush = object()


def diff(a, b):
    # type: (Sequence[str], Iterable[str]) -> Iterator[Union[Ins, Del, FlushT]]
    a_index = 0
    b_index = -1  # init in case b is empty
    len_a = len(a)
    a_set = set(a)
    for b_index, line in enumerate(b):
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


if MYPY:
    ShouldAbort = Callable[[], bool]
    Runners = Dict[sublime.BufferId, ShouldAbort]
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


def log_git_command(fn):
    # type: (Callable[..., Iterator[T]]) -> Callable[..., Iterator[T]]
    def decorated(self, *args, **kwargs):
        start_time = time.perf_counter()
        stderr = ''
        saved_exception = None
        try:
            yield from fn(self, *args, **kwargs)
        except GitSavvyError as e:
            stderr = e.stderr
            saved_exception = e
        finally:
            end_time = time.perf_counter()
            util.debug.log_git(args, None, "<SNIP>", stderr, end_time - start_time)
            if saved_exception:
                raise saved_exception from None
    return decorated


class Done(Exception):
    pass


if MYPY:
    class SimpleFiniteQueue(Generic[T]):
        def consume(self, it: Iterable[T]) -> None: ...  # noqa: E704
        def _put(self, item: T) -> None: ...  # noqa: E704
        def get(self, block=True, timeout=float) -> T: ...  # noqa: E704
else:
    TheEnd = object()

    class SimpleFiniteQueue:
        def __init__(self):
            self._queue = deque()
            self._count = threading.Semaphore(0)

        def consume(self, it):
            try:
                for item in it:
                    self._put(item)
            finally:
                self._put(TheEnd)

        def _put(self, item):
            self._queue.append(item)
            self._count.release()

        def get(self, block=True, timeout=None):
            if not self._count.acquire(block, timeout):
                raise Empty
            val = self._queue.popleft()
            if val is TheEnd:
                raise Done
            else:
                return val


def try_kill_proc(proc):
    if proc:
        utils.kill_proc(proc)


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


class gs_log_graph_refresh(TextCommand, GitCommand):

    """
    Refresh the current graph view with the latest commits.
    """

    def run(self, edit, navigate_after_draw=False):
        # type: (object, bool) -> None
        # Edge case: If you restore a workspace/project, the view might still be
        # loading and hence not ready for refresh calls.
        if self.view.is_loading():
            return
        should_abort = make_aborter(self.view)
        enqueue_on_worker(self.run_impl, should_abort, navigate_after_draw)

    def format_line(self, line):
        return re.sub(
            r'(^[{}]*)\*'.format(GRAPH_CHAR_OPTIONS),
            r'\1' + COMMIT_NODE_CHAR,
            line,
            flags=re.MULTILINE
        )

    def run_impl(self, should_abort, navigate_after_draw=False):
        prelude_text = prelude(self.view)
        initial_draw = self.view.size() == 0
        if initial_draw:
            replace_view_content(self.view, prelude_text, sublime.Region(0, 1))

        try:
            current_graph = self.view.substr(
                self.view.find_by_selector('meta.content.git_savvy.graph')[0]
            )
        except IndexError:
            current_graph = ''
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
                    try_kill_proc(current_proc)
                else:
                    return fn(*args, **kwargs)
            return decorated

        def reader():
            next_graph_splitted = chain(
                map(self.format_line, self.read_graph(got_proc=remember_proc)),
                ['\n']
            )
            tokens = normalize_tokens(simplify(
                diff(current_graph_splitted, next_graph_splitted),
                max_size=100
            ))
            if (
                initial_draw
                and self.view.settings().get('git_savvy.log_graph_view.decoration') == 'sparse'
            ):
                # On large repos (e.g. the "git" repo) "--sparse" can be excessive to compute
                # upfront t.i. before the first byte. For now, just race with a timeout and
                # maybe fallback.
                try:
                    tokens = run_or_timeout(lambda: wait_for_first_item(tokens), timeout=1.0)
                except TimeoutError:
                    try_kill_proc(current_proc)
                    self.view.settings().set('git_savvy.log_graph_view.decoration', None)
                    enqueue_on_worker(self.view.run_command, "gs_log_graph_refresh")
                    return
            else:
                tokens = wait_for_first_item(tokens)
            enqueue_on_ui(draw)
            token_queue.consume(tokens)

        @ensure_not_aborted
        def draw():
            sel = get_simple_selection(self.view)
            if sel is None:
                follow, col_range = None, None
            else:
                follow = self.view.settings().get('git_savvy.log_graph_view.follow')
                col_range = get_column_range(self.view, sel)
            visible_selection = is_sel_in_viewport(self.view)

            current_prelude_region = self.view.find_by_selector('meta.prelude.git_savvy.graph')[0]
            replace_view_content(self.view, prelude_text, current_prelude_region)
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
                    elif navigate_after_draw:  # on init
                        view.run_command("gs_log_graph_navigate")
                        painter_state.set('navigated')
                    elif selection_is_before_region(view, region):
                        painter_state.set('navigated')

                if painter_state == 'navigated':
                    if region.end() >= view.visible_region().end():
                        painter_state.set('viewport_readied')

                if block_time.passed(13 if painter_state == 'viewport_readied' else 1000):
                    enqueue_on_worker(call_again)
                    return

            if painter_state == 'initial':
                # If we still did not navigate the symbol is either
                # gone, or happens to be after the fold of fresh
                # content.
                if not follow or not try_navigate_to_symbol():
                    if visible_selection:
                        view.show(view.sel(), True)

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

    @log_git_command
    def git_stdout(self, *args, show_panel_on_stderr=True, throw_on_stderr=True, got_proc=None, **kwargs):
        # type: (...) -> Iterator[str]
        # Note: Can't use `self.decode_stdout` because it blocks the
        # main thread!
        decode = decoder(self.savvy_settings)
        proc = self.git(*args, just_the_proc=True, **kwargs)
        if got_proc:
            got_proc(proc)
        received_some_stdout = False
        with proc:
            while True:
                # Block size 2**14 taken from Sublime's `exec.py`. This
                # may be a hint on how much chars Sublime can draw efficiently.
                # But here we don't draw every line (except initially) but
                # a diff. So we oscillate between getting a first meaningful
                # content fast and not blocking too much here.
                # TODO: `len(lines)` could be a good indicator of how fast
                # the system currently is because it seems to vary a lot when
                # comapring rather short or long (in count of commits) repos.
                lines = proc.stdout.readlines(2**14)
                if not lines:
                    break
                elif not received_some_stdout:
                    received_some_stdout = True
                for line in lines:
                    yield decode(line)

            stderr = ''.join(map(decode, proc.stderr.readlines()))

        if throw_on_stderr and stderr:
            stdout = "<STDOUT SNIPPED>\n" if received_some_stdout else ""
            raise GitSavvyError(
                "$ {}\n\n{}".format(
                    " ".join(["git"] + list(filter(None, args))),
                    ''.join([stdout, stderr])
                ),
                cmd=proc.args,
                stdout=stdout,
                stderr=stderr,
                show_panel=show_panel_on_stderr
            )

    def read_graph(self, got_proc=None):
        # type: (Callable[[subprocess.Popen], None]) -> Iterator[str]
        global DATE_FORMAT, DATE_FORMAT_STATE

        args = self.build_git_command()
        if DATE_FORMAT_STATE == 'trying':
            try:
                yield from self.git_stdout(
                    *args,
                    throw_on_stderr=True,
                    show_status_message_on_stderr=False,
                    show_panel_on_stderr=False,
                    got_proc=got_proc
                )
            except GitSavvyError as e:
                if e.stderr and DATE_FORMAT in e.stderr:
                    DATE_FORMAT = FALLBACK_DATE_FORMAT
                    DATE_FORMAT_STATE = 'final'
                    enqueue_on_worker(self.view.run_command, "gs_log_graph_refresh")
                    return iter('')
                else:
                    raise GitSavvyError(
                        e.message,
                        cmd=e.cmd,
                        stdout=e.stdout,
                        stderr=e.stderr,
                        show_panel=True,
                    )
            else:
                DATE_FORMAT_STATE = 'final'

        else:
            yield from self.git_stdout(*args, got_proc=got_proc)

    def build_git_command(self):
        global DATE_FORMAT

        settings = self.view.settings()
        follow = self.savvy_settings.get("log_follow_rename")
        author = settings.get("git_savvy.log_graph_view.filter_by_author")
        all_branches = settings.get("git_savvy.log_graph_view.all_branches")
        fpath = self.file_path
        args = [
            'log',
            '--graph',
            '--decorate',  # set explicitly for "decorate-refs-exclude" to work
            '--date={}'.format(DATE_FORMAT),
            '--pretty=format:%h%d %<|(80,trunc)%s | %ad, %an',
            '--follow' if fpath and follow else None,
            '--author={}'.format(author) if author else None,
            '--decorate-refs-exclude=refs/remotes/origin/HEAD',  # cosmetics
            '--exclude=refs/stash',
            '--all' if all_branches else None,
        ]

        if not fpath and settings.get('git_savvy.log_graph_view.decoration') == 'sparse':
            args += ['--simplify-by-decoration', '--sparse']

        branches = settings.get("git_savvy.log_graph_view.branches")
        if branches:
            args += branches

        filters = settings.get("git_savvy.log_graph_view.filters")
        if filters:
            args += shlex.split(filters)

        if fpath:
            args += ["--", self.get_rel_path(fpath)]

        return args


locally_preferred_encoding = locale.getpreferredencoding()


def decoder(settings):
    encodings = ['utf8', locally_preferred_encoding, settings.get("fallback_encoding")]

    def decode(bytes):
        # type: (bytes) -> str
        for encoding in encodings:
            try:
                return bytes.decode(encoding)
            except UnicodeDecodeError:
                pass
        return bytes.decode('utf8', errors='replace')
    return decode


def prelude(view):
    # type: (sublime.View) -> str
    prelude = "\n"
    settings = view.settings()
    repo_path = settings.get("git_savvy.repo_path")
    file_path = settings.get("git_savvy.file_path")
    if file_path:
        rel_file_path = os.path.relpath(file_path, repo_path)
        prelude += "  FILE: {}\n".format(rel_file_path)
    elif repo_path:
        prelude += "  REPO: {}\n".format(repo_path)

    all_ = settings.get("git_savvy.log_graph_view.all_branches") or False
    branches = settings.get("git_savvy.log_graph_view.branches") or []
    filters = settings.get("git_savvy.log_graph_view.filters") or ""
    prelude += (
        "  "
        + "  ".join(filter(None, [
            '[a]ll: true' if all_ else '[a]ll: false',
            " ".join(branches),
            filters
        ]))
        + "\n"
    )
    return prelude + "\n"


class gs_log_graph(GsLogCommand):
    """
    Defines the main menu if you invoke `git: graph` or `git: graph current file`.

    Accepts `current_file: bool` or `file_path: str` as (keyword) arguments, and
    ensures that each of the defined actions/commands in `default_actions` are finally
    called with `file_path` set.
    """
    default_actions = [  # type: ignore[assignment]
        ["gs_log_graph_current_branch", "For current branch"],
        ["gs_log_graph_all_branches", "For all branches"],
        ["gs_log_graph_by_author", "Filtered by author"],
        ["gs_log_graph_by_branch", "Filtered by branch"],
    ]


class gs_log_graph_current_branch(WindowCommand, GitCommand):
    def run(self, file_path=None):
        self.window.run_command('gs_graph', {
            'file_path': file_path,
            'all': True,
            'follow': 'HEAD',
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
            self.window.run_command(
                'gs_graph',
                {'file_path': file_path, 'author': selected_author}
            )

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
            if branch:
                self._selected_branch = branch  # remember last selection
                self.window.run_command('gs_graph', {
                    'file_path': file_path,
                    'all': True,
                    'branches': [branch],
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
            line_span = view.line(view.text_point(row_, 0))
            if len(line_span) == 0:
                break

            commit_hash_region = extract_comit_hash_span(view, line_span)
            if not commit_hash_region:
                continue

            if not natural_movement:
                return commit_hash_region

            col_ = commit_hash_region.b - line_span.a
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
            cur_dot = next(_find_dots(view))
        except StopIteration:
            view.run_command("gs_log_graph_navigate", {"forward": forward})
            return

        next_dots = follow_first_parent_commit(cur_dot, forward)
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

        line_span = view.line(next_dot.region())
        r = extract_comit_hash_span(view, line_span)
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


def follow_first_parent_commit(dot, forward):
    # type: (colorizer.Char, bool) -> Iterator[colorizer.Char]
    fn = colorizer.follow_path_down if forward else colorizer.follow_path_up
    while True:
        dot = next(ch for ch in fn(dot) if ch == COMMIT_NODE_CHAR)
        yield dot


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
        settings = self.view.settings()
        filters = settings.get("git_savvy.log_graph_view.filters", "")
        filter_history = settings.get("git_savvy.log_graph_view.filter_history")
        if not filter_history:
            filter_history = DEFAULT_HISTORY_ENTRIES + ([filters] if filters else [])
        elif not filters:
            filters = filter_history[-1]

        def on_done(text):
            # type: (str) -> None
            new_filter_history = (
                filter_history
                if text in filter_history or not text
                else (filter_history + [text])
            )
            settings.set("git_savvy.log_graph_view.filters", text)
            settings.set("git_savvy.log_graph_view.filter_history", new_filter_history)

            hide_toast()  # type: ignore[has-type]
            self.view.run_command("gs_log_graph_refresh")

        input_panel = show_single_line_input_panel(
            "additional args",
            filters,
            on_done,
            on_cancel=lambda: enqueue_on_worker(hide_toast),  # type: ignore[has-type]
            select_text=True
        )

        input_panel_settings = input_panel.settings()
        input_panel_settings.set("input_panel_with_history", True)
        input_panel_settings.set("input_panel_with_history.entries", filter_history)
        input_panel_settings.set("input_panel_with_history.active", index_of(filter_history, filters, -1))

        hide_toast = show_toast(
            self.view,
            "↑↓ for the history\n"
            "Examples:  -Ssearch_term  |  -Gsearch_term  ",
            timeout=-1
        )


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
        settings.set("git_savvy.log_graph_view.filters", "")
        self.view.run_command("gs_log_graph_refresh")


class gs_log_graph_toggle_all_setting(TextCommand, GitCommand):
    def run(self, edit):
        settings = self.view.settings()
        current = settings.get("git_savvy.log_graph_view.all_branches")
        next_state = not current
        settings.set("git_savvy.log_graph_view.all_branches", next_state)
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
        line_span = self.view.line(sel)
        line_text = self.view.substr(line_span)
        commit_hash = extract_commit_hash(line_text)
        if not commit_hash:
            return

        window.run_command("gs_show_commit", {"commit_hash": commit_hash})


class GsLogGraphCursorListener(EventListener, GitCommand):
    def is_applicable(self, view):
        # type: (sublime.View) -> bool
        return bool(view.settings().get("git_savvy.log_graph_view"))

    def on_activated(self, view):
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
        if not self.is_applicable(view) and show_commit_info.panel_is_visible(window):
            panel = PREVIOUS_OPEN_PANEL_PER_WINDOW.get(window.id(), None)
            if panel:
                window.run_command("show_panel", {"panel": panel})
            else:
                window.run_command('hide_panel')

        # Auto-show panel if the user switches back
        elif (
            self.is_applicable(view)
            and not show_commit_info.panel_is_visible(window)
            and view.settings().get("git_savvy.log_graph_view.show_commit_info_panel")
        ):
            window.run_command("show_panel", {"panel": "output.show_commit_info"})

    # `on_selection_modified` triggers twice per mouse click
    # multiplied with the number of views into the same buffer,
    # hence it is *important* to throttle these events.
    # We do this seperately per side-effect. See the fn
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
            # incremantal find panel via `<enter>` will bring the commit panel up
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
    symbol = extract_symbol_to_follow(view)
    if symbol:
        view.settings().set('git_savvy.log_graph_view.follow', symbol)


def extract_symbol_to_follow(view):
    # type: (sublime.View) -> Optional[str]
    """Extract a symbol to follow."""
    try:
        # Intentional `b` (not `end()`!) because b is where the
        # cursor is. (If you select upwards b becomes < a.)
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return None

    line_span = view.line(cursor)
    line_text = view.substr(line_span)
    return _extract_symbol_to_follow(view.id(), line_text)


@lru_cache(maxsize=512)
def _extract_symbol_to_follow(vid, _line_text):
    # type: (sublime.ViewId, str) -> Optional[str]
    view = sublime.View(vid)
    try:
        # Intentional `b` (not `end()`!) because b is where the
        # cursor is. (If you select upwards b becomes < a.)
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return None

    line_span = view.line(cursor)
    if view.match_selector(cursor, 'meta.graph.graph-line.head.git-savvy'):
        return 'HEAD'

    symbols_on_line = [
        symbol
        for r, symbol in view.symbols()
        if line_span.contains(r)
    ]
    if symbols_on_line:
        # git always puts the remotes first so we take
        # the last one which is (then) a local branch.
        return symbols_on_line[-1]

    line_text = view.substr(line_span)
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
            line_of_symbol = view.line(r)
            return extract_comit_hash_span(view, line_of_symbol)

    r = view.find(FIND_COMMIT_HASH + re.escape(symbol), 0)
    if not r.empty():
        line_of_symbol = view.line(r)
        return extract_comit_hash_span(view, line_of_symbol)
    return None


def extract_comit_hash_span(view, line_span):
    # type: (sublime.View, sublime.Region) -> Optional[sublime.Region]
    line_text = view.substr(line_span)
    match = COMMIT_LINE.search(line_text)
    if match:
        a, b = match.span('commit_hash')
        return sublime.Region(a + line_span.a, b + line_span.a)
    return None


FIND_COMMIT_HASH = "^[{graph_chars}]*[{node_chars}][{graph_chars}]* ".format(
    graph_chars=GRAPH_CHAR_OPTIONS, node_chars=COMMIT_NODE_CHAR_OPTIONS
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
    dots = tuple(find_dots(view))
    _colorize_dots(view.id(), dots)


def find_dots(view):
    # type: (sublime.View) -> Set[colorizer.Char]
    return set(_find_dots(view))


def _find_dots(view):
    # type: (sublime.View) -> Iterator[colorizer.Char]
    for s in view.sel():
        line_region = view.line(s.begin())
        line_content = view.substr(line_region)
        idx = line_content.find(COMMIT_NODE_CHAR)
        if idx > -1:
            yield colorizer.Char(view, line_region.begin() + idx)


@lru_cache(maxsize=1)
# ^- throttle side-effects
def _colorize_dots(vid, dots):
    # type: (sublime.ViewId, Tuple[colorizer.Char]) -> None
    view = sublime.View(vid)
    to_region = lambda ch: ch.region()  # type: Callable[[colorizer.Char], sublime.Region]

    view.add_regions('gs_log_graph.dot', list(map(to_region, dots)), scope=DOT_SCOPE)
    path_down = flatten(filter(
        lambda path: len(path) > 1,  # type: ignore[arg-type]  # https://github.com/python/mypy/issues/9176
        map(colorizer.follow_path_down, dots)
    ))
    view.add_regions('gs_log_graph.path_below', list(map(to_region, path_down)), scope=PATH_SCOPE)

    chars_up = flatten(filter(
        lambda path: len(path) > 1,  # type: ignore[arg-type]  # https://github.com/python/mypy/issues/9176
        map(colorizer.follow_path_up, dots)
    ))
    path_up, dot_up = partition(lambda ch: ch == COMMIT_NODE_CHAR, chars_up)
    view.add_regions('gs_log_graph.path_above', list(map(to_region, path_up)), scope=PATH_ABOVE_SCOPE)
    view.add_regions('gs_log_graph.dot.above', list(map(to_region, dot_up)), scope=DOT_ABOVE_SCOPE)


def colorize_fixups(view):
    # type: (sublime.View) -> None
    dots = tuple(find_dots(view))
    _colorize_fixups(view.id(), dots)


@lru_cache(maxsize=1)
def _colorize_fixups(vid, dots):
    # type: (sublime.ViewId, Tuple[colorizer.Char]) -> None
    view = sublime.View(vid)
    extract_message = partial(message_from_fixup_squash_line, view.id())
    matching_dots = list(filter_(
        find_matching_commit(view.id(), dot, message)
        for dot, message in zip(dots, map(extract_message, dots))
        if message
    ))
    view.add_regions(
        'gs_log_graph_follow_fixups',
        [dot.region() for dot in matching_dots],
        scope=MATCHING_COMMIT_SCOPE
    )


def extract_message_regions(view):
    # type: (sublime.View) -> List[sublime.Region]
    return find_by_selector(view, "meta.graph.message.git-savvy")


def find_by_selector(view, selector):
    # type: (sublime.View, str) -> List[sublime.Region]
    # Same as `view.find_by_selector` but cached.
    return _find_by_selector(view.id(), view.change_count(), selector)


@lru_cache(maxsize=16)
def _find_by_selector(vid, _cc, selector):
    # type: (sublime.ViewId, int, str) -> List[sublime.Region]
    view = sublime.View(vid)
    return view.find_by_selector(selector)


@lru_cache(maxsize=64)
def message_from_fixup_squash_line(vid, dot):
    # type: (sublime.ViewId, colorizer.Char) -> Optional[str]
    view = sublime.View(vid)
    message = commit_message_from_point(view, dot.pt)
    if not message:
        return None
    # Truncated messages end with one or multiple "." dots which we
    # have to strip.
    if message.startswith('fixup! '):
        return message[7:].rstrip('.').strip()
    if message.startswith('squash! '):
        return message[8:].rstrip('.').strip()
    return None


def commit_message_from_point(view, pt):
    # type: (sublime.View, int) -> Optional[str]
    line_span = view.line(pt)
    for r in extract_message_regions(view):
        if line_span.contains(r):
            return view.substr(r)
    else:
        return None


@lru_cache(maxsize=64)
def find_matching_commit(vid, dot, message):
    # type: (sublime.ViewId, colorizer.Char, str) -> Optional[colorizer.Char]
    view = sublime.View(vid)
    for dot in islice(follow_dots(dot), 0, 50):
        this_message = commit_message_from_point(view, dot.pt)
        if this_message and this_message.startswith(message):
            return dot
    else:
        return None


def follow_dots(dot):
    # type: (colorizer.Char) -> Iterator[colorizer.Char]
    """Follow dot to dot omitting the path chars in between."""
    while True:
        dot = next(ch for ch in colorizer.follow_path_down(dot) if ch == COMMIT_NODE_CHAR)
        yield dot


def draw_info_panel(view):
    # type: (sublime.View) -> None
    """Extract line under the last cursor and draw info panel."""
    try:
        # Intentional `b` (not `end()`!) because b is where the
        # cursor is. (If you select upwards b becomes < a.)
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return

    line_span = view.line(cursor)
    line_text = view.substr(line_span)

    # Defer to a second fn to reduce side-effects
    draw_info_panel_for_line(view.id(), line_text)


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
        window.run_command("gs_show_commit_info", {"commit_hash": commit_hash})


def extract_commit_hash(line):
    # type: (str) -> str
    match = COMMIT_LINE.search(line)
    return match.group('commit_hash') if match else ""


class gs_log_graph_toggle_more_info(WindowCommand, GitCommand):

    """
    Toggle commit info output panel.
    """

    def run(self):
        if show_commit_info.panel_is_visible(self.window):
            self.window.run_command("hide_panel", {"panel": "output.show_commit_info"})
        else:
            self.window.run_command("show_panel", {"panel": "output.show_commit_info"})


if MYPY:
    from typing import Literal, TypedDict
    LineInfo = TypedDict('LineInfo', {
        'commit': str,
        'HEAD': str,
        'branches': List[str],
        'local_branches': List[str],
        'tags': List[str],
    }, total=False)
    ListItems = Literal["branches", "local_branches", "tags"]


def describe_graph_line(line, remotes):
    # type: (str, Iterable[str]) -> Optional[LineInfo]
    match = COMMIT_LINE.match(line)
    if match is None:
        return None

    commit_hash = match.group("commit_hash")
    decoration = match.group("decoration")

    rv = {"commit": commit_hash}  # type: LineInfo
    if decoration:
        decoration = decoration[1:-1]  # strip parentheses
        names = decoration.split(", ")
        if names[0].startswith("HEAD"):
            head, *names = names
            if head == "HEAD":
                rv["HEAD"] = commit_hash
            else:
                branch = head[len("HEAD -> "):]
                rv["HEAD"] = branch
                names = [branch] + names
        branches, local_branches, tags = [], [], []
        for name in names:
            if name.startswith("tag: "):
                tags.append(name[len("tag: "):])
            else:
                branches.append(name)
                if not any(name.startswith(remote + "/") for remote in remotes):
                    local_branches.append(name)
        if branches:
            rv["branches"] = branches
        if local_branches:
            rv["local_branches"] = local_branches
        if tags:
            rv["tags"] = tags

    return rv


def describe_head(view, remotes):
    # type: (sublime.View, Iterable[str]) -> Optional[LineInfo]
    try:
        region = view.find_by_selector(
            'meta.graph.graph-line.head.git-savvy '
            'constant.numeric.graph.commit-hash.git-savvy'
        )[0]
    except IndexError:
        return None

    cursor = region.b
    line_span = view.line(cursor)
    line_text = view.substr(line_span)
    return describe_graph_line(line_text, remotes)


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

        remotes = set(self.get_remotes().keys())
        infos = list(filter_(
            describe_graph_line(line, remotes)
            for line in unique(
                view.substr(line)
                for s in view.sel()
                for line in view.lines(s)
            )
        ))
        if not infos:
            return

        actions = (
            self.actions_for_single_line(view, infos[0], remotes)
            if len(infos) == 1
            else self.actions_for_multiple_lines(view, infos, remotes)
        )
        if not actions:
            return

        def on_action_selection(index):
            if index == -1:
                return

            self.selected_index = index
            description, action = actions[index]
            action()

        self.window.show_quick_panel(
            [a[0] for a in actions],
            on_action_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=self.selected_index,
        )

    def actions_for_multiple_lines(self, view, infos, remotes):
        # type: (sublime.View, List[LineInfo], Iterable[str]) -> List[Tuple[str, Callable[[], None]]]
        file_path = self.file_path
        actions = []  # type: List[Tuple[str, Callable[[], None]]]

        sel = view.sel()
        if all(s.empty() for s in sel) and len(sel) == 2:
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

            base_commit = display_name(infos[0])
            target_commit = display_name(infos[1])

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
                    partial(self.compare_against, base_commit, target_commit, file_path)
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

    def actions_for_single_line(self, view, info, remotes):
        # type: (sublime.View, LineInfo, Iterable[str]) -> List[Tuple[str, Callable[[], None]]]
        commit_hash = info["commit"]
        file_path = self.file_path
        actions = []  # type: List[Tuple[str, Callable[[], None]]]
        on_checked_out_branch = "HEAD" in info and info["HEAD"] in info["local_branches"]
        if on_checked_out_branch:
            actions += [
                ("Pull", self.pull),
                ("Push", partial(self.push, info["HEAD"])),
                ("Fetch", partial(self.fetch, info["HEAD"])),
                ("-" * 75, self.noop),
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
            ("Create tag", partial(self.create_tag, commit_hash))
        ]
        actions += [
            ("Delete '{}'".format(tag_name), partial(self.delete_tag, tag_name))
            for tag_name in info.get("tags", [])
        ]

        head_info = describe_head(view, remotes)
        head_is_on_a_branch = head_info and head_info["HEAD"] != head_info["commit"]

        def get_list(info, key):
            # type: (LineInfo, ListItems) -> List[str]
            return info.get(key, [])  # type: ignore

        if not head_info or head_info["commit"] != info["commit"]:
            good_head_name = (
                "'{}'".format(head_info["HEAD"])  # type: ignore
                if head_is_on_a_branch
                else "HEAD"
            )
            get = partial(get_list, info)  # type: Callable[[ListItems], List[str]]
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

        if head_info and head_info["commit"] != info["commit"]:
            get = partial(get_list, head_info)  # type: Callable[[ListItems], List[str]]  # type: ignore[no-redef]
            good_move_target = (
                head_info["HEAD"]
                if head_is_on_a_branch
                else next(
                    chain(get("local_branches"), get("branches"), get("tags")),
                    head_info["commit"]
                )
            )
            actions += [
                (
                    "Move '{}' to '{}'".format(branch_name, good_move_target),
                    partial(self.checkout_b, branch_name)
                )
                for branch_name in info.get("local_branches", [])
            ]

        actions += [
            ("Delete branch '{}'".format(branch_name), partial(self.delete_branch, branch_name))
            for branch_name in info.get("local_branches", [])
            if info.get("HEAD") != branch_name
        ]

        if "HEAD" not in info:
            actions += [
                ("Cherry-pick commit", partial(self.cherry_pick, commit_hash)),
            ]

        actions += [
            ("Revert commit", partial(self.revert_commit, commit_hash)),
            (
                "Compare {}against ...".format("file " if file_path else ""),
                partial(self.compare_against, commit_hash, file_path=file_path)
            ),
        ]
        if file_path:
            actions += [
                (
                    "Diff file against workdir",
                    partial(self.diff_commit, commit_hash)
                ),
            ]
        elif on_checked_out_branch:
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

    def pull(self):
        self.window.run_command("gs_pull")

    def push(self, current_branch):
        self.window.run_command("gs_push", {"local_branch_name": current_branch})

    def fetch(self, current_branch):
        remote = self.get_remote_for_branch(current_branch)
        self.window.run_command("gs_fetch", {"remote": remote} if remote else None)

    def noop(self):
        return

    def checkout(self, commit_hash):
        self.git("checkout", commit_hash)
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def checkout_b(self, branch_name):
        self.git("checkout", "-B", branch_name)
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def delete_branch(self, branch_name):
        self.window.run_command("gs_delete_branch", {"branch": branch_name})

    def show_commit(self, commit_hash):
        self.window.run_command("gs_show_commit", {"commit_hash": commit_hash})

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
        try:
            self.git("revert", *commit_hash)
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def compare_against(self, base_commit, target_commit=None, file_path=None):
        self.window.run_command("gs_compare_against", {
            "base_commit": base_commit,
            "target_commit": target_commit,
            "file_path": file_path
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
