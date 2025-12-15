from __future__ import annotations

from collections import deque
from functools import lru_cache, partial
from itertools import chain, groupby, islice
from queue import Empty
import re
import shlex
import subprocess
import textwrap
import threading
from typing import (
    Callable,
    Deque,
    Dict,
    Generic,
    Iterable,
    Iterator,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypeVar,
    Union,
    TYPE_CHECKING,
)

import sublime

from .. import store, utils
from ..base_commands import GsTextCommand
from ..fns import filter_, pairwise, take
from ..git_command import GitSavvyError
from ..runtime import (
    enqueue_on_ui,
    enqueue_on_worker,
    run_and_check_timeout,
    run_on_new_thread,
    run_or_timeout,
    text_command,
    time_budget,
)
from ..ui__busy_spinner import start_busy_indicator, stop_busy_indicator
from ..view import replace_view_content, visible_views
from .log_graph import (
    get_simple_selection,
    just_set_cursor,
    navigate_to_symbol,
    set_and_show_cursor,
)
from .log_graph_helper import (
    COMMIT_LINE,
    DEFAULT_NODE_CHAR,
    FIND_COMMIT_HASH,
    GRAPH_HEIGHT,
    ROOT_NODE_CHAR,
)

T = TypeVar("T")

__all__ = ("gs_log_graph_refresh",)

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
        remotes = self.current_state().get("remotes") or self.get_remotes()
        if overview:
            show_tags = settings.get("git_savvy.log_graph_view.show_tags")
            args = [
                'log',
                '--graph',
                '--decorate',  # set explicitly for "decorate-refs-exclude" to work
                *[
                    # cosmetics
                    f'--decorate-refs-exclude=refs/remotes/{remote}/HEAD'
                    for remote in remotes
                ],
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
            *[
                # cosmetics
                f'--decorate-refs-exclude=refs/remotes/{remote}/HEAD'
                for remote in remotes
            ],
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


def get_column_range(view, region):
    # type: (sublime.View, sublime.Region) -> Tuple[int, int]
    line_start = view.line(region).begin()
    return (region.begin() - line_start, region.end() - line_start)


def is_sel_in_viewport(view):
    # type: (sublime.View) -> bool
    viewport = view.visible_region()
    return all(viewport.contains(s) or viewport.intersects(s) for s in view.sel())
