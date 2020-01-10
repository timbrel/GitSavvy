"""
Implements a special view to visualize and stage pieces of a project's
current diff.
"""

from collections import namedtuple
from contextlib import contextmanager
from functools import lru_cache, partial
import inspect
from itertools import accumulate as accumulate_, chain, groupby, tee, zip_longest
import difflib
import os
import re
import time
import threading

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from .navigate import GsNavigate
from ..git_command import GitCommand
from ..exceptions import GitSavvyError
from ...common import util
filter_ = partial(filter, None)  # type: Callable[[Iterator[Optional[T]]], Iterator[T]]
flatten = chain.from_iterable


MYPY = False
if MYPY:
    from typing import (
        Callable, Dict, Iterable, Iterator, List, Literal, NamedTuple, Optional, Set, Sequence,
        Tuple, Type, TypeVar, Union
    )

    T = TypeVar('T')
    Point = int
    RowCol = Tuple[int, int]

    Chunk = List['HunkLine']


DIFF_TITLE = "DIFF: {}"
DIFF_CACHED_TITLE = "DIFF (cached): {}"

# Clickable lines:
# (A)  common/commands/view_manipulation.py  |   1 +
# (B) --- a/common/commands/view_manipulation.py
# (C) +++ b/common/commands/view_manipulation.py
# (D) diff --git a/common/commands/view_manipulation.py b/common/commands/view_manipulation.py
FILE_RE = (
    r"^(?:\s(?=.*\s+\|\s+\d+\s)|--- a\/|\+{3} b\/|diff .+b\/)"
    #     ^^^^^^^^^^^^^^^^^^^^^ (A)
    #     ^ one space, and then somewhere later on the line the pattern `  |  23 `
    #                           ^^^^^^^ (B)
    #                                   ^^^^^^^^ (C)
    #                                            ^^^^^^^^^^^ (D)
    r"(\S[^|]*?)"
    #         ^ ! lazy to not match the trailing spaces, see below

    r"(?:\s+\||$)"
    #          ^ (B), (C), (D)
    #    ^^^^^ (A) We must match the spaces here bc Sublime will not rstrip() the
    #    filename for us.
)

# Clickable line:
# @@ -69,6 +69,7 @@ class GsHandleVintageousCommand(TextCommand):
#           ^^ we want the second (current) line offset of the diff
LINE_RE = r"^@@ [^+]*\+(\d+)"

diff_views = {}  # type: Dict[str, sublime.View]


class GsDiffCommand(WindowCommand, GitCommand):

    """
    Create a new view to display the difference of `target_commit`
    against `base_commit`. If `target_commit` is None, compare
    working directory with `base_commit`.  If `in_cached_mode` is set,
    display a diff of the Git index. Set `disable_stage` to True to
    disable Ctrl-Enter in the diff view.
    """

    def run(self, in_cached_mode=False, file_path=None, current_file=False, base_commit=None,
            target_commit=None, disable_stage=False, title=None):
        repo_path = self.repo_path
        if current_file:
            file_path = self.file_path or file_path

        view_key = "{0}{1}+{2}".format(
            in_cached_mode,
            "-" if base_commit is None else "--" + base_commit,
            file_path or repo_path
        )

        if view_key in diff_views and diff_views[view_key] in sublime.active_window().views():
            diff_view = diff_views[view_key]
            self.window.focus_view(diff_view)

        else:
            diff_view = util.view.get_scratch_view(self, "diff", read_only=True)

            settings = diff_view.settings()
            settings.set("git_savvy.repo_path", repo_path)
            settings.set("git_savvy.file_path", file_path)
            settings.set("git_savvy.diff_view.in_cached_mode", in_cached_mode)
            settings.set("git_savvy.diff_view.ignore_whitespace", False)
            settings.set("git_savvy.diff_view.show_word_diff", False)
            settings.set("git_savvy.diff_view.context_lines", 3)
            settings.set("git_savvy.diff_view.base_commit", base_commit)
            settings.set("git_savvy.diff_view.target_commit", target_commit)
            settings.set("git_savvy.diff_view.show_diffstat", self.savvy_settings.get("show_diffstat", True))
            settings.set("git_savvy.diff_view.disable_stage", disable_stage)
            settings.set("git_savvy.diff_view.history", [])
            settings.set("git_savvy.diff_view.just_hunked", "")

            settings.set("result_file_regex", FILE_RE)
            settings.set("result_line_regex", LINE_RE)
            settings.set("result_base_dir", repo_path)

            if not title:
                title = (DIFF_CACHED_TITLE if in_cached_mode else DIFF_TITLE).format(
                    os.path.basename(file_path) if file_path else os.path.basename(repo_path)
                )
            diff_view.set_name(title)
            diff_view.set_syntax_file("Packages/GitSavvy/syntax/diff_view.sublime-syntax")
            diff_views[view_key] = diff_view

            diff_view.run_command("gs_handle_vintageous")


WORD_DIFF_PATTERNS = [
    None,
    r"[a-zA-Z_\-\x80-\xff]+|[^[:space:]]|[\xc0-\xff][\x80-\xbf]+",
    ".",
]
WORD_DIFF_MARKERS_RE = re.compile(r"{\+(.*?)\+}|\[-(.*?)-\]")


class GsDiffRefreshCommand(TextCommand, GitCommand):
    """Refresh the diff view with the latest repo state."""

    def run(self, edit, sync=True):
        if sync:
            self._run()
        else:
            sublime.set_timeout_async(self._run)

    def _run(self):
        if self.view.settings().get("git_savvy.disable_diff"):
            return
        in_cached_mode = self.view.settings().get("git_savvy.diff_view.in_cached_mode")
        ignore_whitespace = self.view.settings().get("git_savvy.diff_view.ignore_whitespace")
        show_word_diff = self.view.settings().get("git_savvy.diff_view.show_word_diff")
        base_commit = self.view.settings().get("git_savvy.diff_view.base_commit")
        target_commit = self.view.settings().get("git_savvy.diff_view.target_commit")
        show_diffstat = self.view.settings().get("git_savvy.diff_view.show_diffstat")
        disable_stage = self.view.settings().get("git_savvy.diff_view.disable_stage")
        context_lines = self.view.settings().get('git_savvy.diff_view.context_lines')

        word_diff_regex = WORD_DIFF_PATTERNS[show_word_diff]

        prelude = "\n"
        if self.file_path:
            rel_file_path = os.path.relpath(self.file_path, self.repo_path)
            prelude += "  FILE: {}\n".format(rel_file_path)

        if disable_stage:
            if in_cached_mode:
                prelude += "  INDEX..{}\n".format(base_commit or target_commit)
            else:
                if base_commit and target_commit:
                    prelude += "  {}..{}\n".format(base_commit, target_commit)
                else:
                    prelude += "  WORKING DIR..{}\n".format(base_commit or target_commit)
        else:
            if in_cached_mode:
                prelude += "  STAGED CHANGES (Will commit)\n"
            else:
                prelude += "  UNSTAGED CHANGES\n"

        if show_word_diff:
            prelude += "  WORD REGEX: {}\n".format(word_diff_regex)
        if ignore_whitespace:
            prelude += "  IGNORING WHITESPACE\n"

        try:
            diff = self.git(
                "diff",
                "--ignore-all-space" if ignore_whitespace else None,
                "--word-diff-regex={}".format(word_diff_regex) if word_diff_regex else None,
                "--unified={}".format(context_lines) if context_lines is not None else None,
                "--stat" if show_diffstat else None,
                "--patch",
                "--no-color",
                "--cached" if in_cached_mode else None,
                base_commit,
                target_commit,
                "--", self.file_path)
        except GitSavvyError as err:
            # When the output of the above Git command fails to correctly parse,
            # the expected notification will be displayed to the user.  However,
            # once the userpresses OK, a new refresh event will be triggered on
            # the view.
            #
            # This causes an infinite loop of increasingly frustrating error
            # messages, ultimately resulting in psychosis and serious medical
            # bills.  This is a better, though somewhat cludgy, alternative.
            #
            if err.args and type(err.args[0]) == UnicodeDecodeError:
                self.view.settings().set("git_savvy.disable_diff", True)
                return
            raise err

        old_diff = self.view.settings().get("git_savvy.diff_view.raw_diff")
        self.view.settings().set("git_savvy.diff_view.raw_diff", diff)
        text = prelude + '\n--\n' + diff

        if word_diff_regex:
            text, added_regions, removed_regions = postprocess_word_diff(text)
        else:
            added_regions, removed_regions = [], []

        sublime.set_timeout(
            lambda: _draw(
                self.view,
                text,
                bool(word_diff_regex),
                added_regions,
                removed_regions,
                navigate=not old_diff
            )
        )


def _draw(view, text, is_word_diff, added_regions, removed_regions, navigate):
    # type: (sublime.View, str, bool, List[sublime.Region], List[sublime.Region], bool) -> None
    view.run_command(
        "gs_replace_view_text", {"text": text, "restore_cursors": True}
    )
    if navigate:
        view.run_command("gs_diff_navigate")

    if is_word_diff:
        view.add_regions(
            "git-savvy-added-bold", added_regions, scope="diff.inserted.char.git-savvy.diff"
        )
        view.add_regions(
            "git-savvy-removed-bold", removed_regions, scope="diff.deleted.char.git-savvy.diff"
        )
    else:
        annotate_intra_line_differences(view)


def postprocess_word_diff(text):
    # type: (str) -> Tuple[str, List[sublime.Region], List[sublime.Region]]
    added_regions = []  # type: List[sublime.Region]
    removed_regions = []  # type: List[sublime.Region]

    def extractor(match):
        # We generally transform `{+text+}` (and likewise `[-text-]`) into just
        # `text`.
        text = match.group()[2:-2]
        # The `start/end` offsets are based on the original input, so we need
        # to adjust them for the regions we want to draw.
        total_matches_so_far = len(added_regions) + len(removed_regions)
        start, _end = match.span()
        # On each match the original diff is shortened by 4 chars.
        offset = start - (total_matches_so_far * 4)

        regions = added_regions if match.group()[1] == '+' else removed_regions
        regions.append(sublime.Region(offset, offset + len(text)))
        return text

    return WORD_DIFF_MARKERS_RE.sub(extractor, text), added_regions, removed_regions


@contextmanager
def print_runtime(message):
    start_time = time.perf_counter()
    yield
    end_time = time.perf_counter()
    duration = round((end_time - start_time) * 1000)
    thread_name = threading.current_thread().name[0]
    print('{} took {}ms [{}]'.format(message, duration, thread_name))


AWAIT_UI_THREAD = 'AWAIT_UI_THREAD'  # type: Literal["AWAIT_UI_THREAD"]
AWAIT_WORKER = 'AWAIT_WORKER'  # type: Literal["AWAIT_WORKER"]
if MYPY:
    HopperR = Iterator[Union[Literal["AWAIT_UI_THREAD", "AWAIT_WORKER"]]]
    HoperFn = Callable[..., HopperR]


def cooperative_thread_hopper(fn):
    # type: (HoperFn) -> Callable[..., None]
    def tick(gen, send_value=None):
        try:
            rv = gen.send(send_value)
        except StopIteration:
            return
        except Exception as ex:
            raise ex from None

        if rv == AWAIT_UI_THREAD:
            sublime.set_timeout(lambda: tick(gen))
        elif rv == AWAIT_WORKER:
            sublime.set_timeout_async(lambda: tick(gen))

    def decorated(*args, **kwargs):
        gen = fn(*args, **kwargs)
        if inspect.isgenerator(gen):
            tick(gen)

    return decorated


def annotate_intra_line_differences(view):
    # type: (sublime.View) -> None
    # import profile
    # profile.runctx('compute_intra_line_diffs(view)', globals(), locals(), sort='cumtime')
    compute_intra_line_diffs(view)


def view_has_changed_factory(view):
    # type: (sublime.View) -> Callable[[], bool]
    cc = view.change_count()

    def view_has_changed():
        # type: () -> bool
        return not view.is_valid() or view.change_count() != cc

    return view_has_changed


MAX_BLOCK_TIME = 17


def block_time_passed_factory(block_time):
    start_time = time.perf_counter()

    def block_time_passed():
        nonlocal start_time

        end_time = time.perf_counter()
        duration = round((end_time - start_time) * 1000)
        if duration > block_time:
            start_time = time.perf_counter()
            return True
        else:
            return False

    return block_time_passed


@cooperative_thread_hopper
def compute_intra_line_diffs(view):
    # type: (sublime.View) -> HopperR
    diff = SplittedDiff.from_view(view)
    viewport = view.visible_region()
    view_has_changed = view_has_changed_factory(view)
    block_time_passed = block_time_passed_factory(MAX_BLOCK_TIME)

    chunks = filter(is_modification_group, flatten(map(group_non_context_lines, diff.hunks)))
    above_viewport, in_viewport, below_viewport = [], [], []  # type: Tuple[List[Chunk], List[Chunk], List[Chunk]]
    for chunk in chunks:
        chunk_region = compute_chunk_region(chunk)
        container = (
            in_viewport if chunk_region.intersects(viewport)
            else above_viewport if chunk_region < viewport
            else below_viewport
        )
        container.append(chunk)

    from_regions = []
    to_regions = []

    for chunk in in_viewport:
        new_from_regions, new_to_regions = intra_line_diff_for_chunk(chunk)
        from_regions.extend(new_from_regions)
        to_regions.extend(new_to_regions)

    _draw_intra_diff_regions(view, to_regions, from_regions)

    yield AWAIT_WORKER
    if view_has_changed():
        return

    # Consider some chunks [1, 2, 3, 4] where 3 was *in* the viewport and thus
    # rendered immediately. Now, [1, 2] + [4] await their render. The following
    # `zip_longest(reversed` dance generates [2, 4, 1] as the unit of work, t.i.
    # we move from the viewport to the edges (inside-out).
    for chunk in filter_(flatten(zip_longest(reversed(above_viewport), below_viewport))):
        new_from_regions, new_to_regions = intra_line_diff_for_chunk(chunk)
        from_regions.extend(new_from_regions)
        to_regions.extend(new_to_regions)

        if block_time_passed():
            if view_has_changed():
                return
            _draw_intra_diff_regions(view, to_regions, from_regions)
            yield AWAIT_WORKER
            if view_has_changed():
                return

    if view_has_changed():
        return
    _draw_intra_diff_regions(view, to_regions, from_regions)


def _draw_intra_diff_regions(view, added_regions, removed_regions):
    view.add_regions(
        "git-savvy-added-bold", added_regions, scope="diff.inserted.char.git-savvy.diff"
    )
    view.add_regions(
        "git-savvy-removed-bold", removed_regions, scope="diff.deleted.char.git-savvy.diff"
    )


def group_non_context_lines(hunk):
    # type: (Hunk) -> List[Chunk]
    """Return groups of chunks(?) (without context) from a hunk."""
    # A hunk can contain many modifications interleaved
    # with context lines. Return just these modification
    # lines grouped as units.
    # Note: No newline marker lines are just ignored t.i.
    # skipped. Alternatively, `is_context` could mark them
    # as context lines.
    return [
        list(lines)
        for is_context, lines in groupby(
            (
                line
                for line in hunk.content().lines()
                if not line.is_no_newline_marker()  # <==
            ),
            key=lambda line: line.is_context()
        )
        if not is_context
    ]


def is_modification_group(lines):
    # type: (Chunk) -> bool
    """Mark groups which have both + and - modes."""
    # Since these groups are always sorted in git, from a to b,
    # such a group starts with a "-" and ends with a "+".
    return lines[0].mode == '-' and lines[-1].mode == '+'


def compute_chunk_region(lines):
    # type: (Chunk) -> sublime.Region
    return sublime.Region(lines[0].a, lines[-1].b)


def intra_line_diff_for_chunk(group):
    # type: (Chunk) -> Tuple[List[Region], List[Region]]
    from_lines, to_lines = [
        list(lines)
        for mode, lines in groupby(group, key=lambda line: line.mode)
    ]

    algo = (
        intra_diff_line_by_line
        if len(from_lines) == len(to_lines)
        else intra_diff_general_algorithm
    )
    return algo(from_lines, to_lines)


@lru_cache(maxsize=512)
def match_sequences(a, b, is_junk=difflib.IS_CHARACTER_JUNK, max_token_length=250):
    # type: (Sequence, Sequence, Callable[[str], bool], int) -> difflib.SequenceMatcher
    if (len(a) + len(b)) > max_token_length:
        return NullSequenceMatcher(is_junk, a='', b='')
    matches = difflib.SequenceMatcher(is_junk, a=a, b=b)
    matches.ratio()  # for the side-effect of doing the computational work and caching it
    return matches


class NullSequenceMatcher(difflib.SequenceMatcher):
    def ratio(self):
        return 0

    def get_opcodes(self):
        return []


def intra_diff_general_algorithm(from_lines, to_lines):
    # type: (List[HunkLine], List[HunkLine]) -> Tuple[List[Region], List[Region]]
    # Generally, if the two line chunks have different size, try to fit
    # the smaller one into the bigger, or try to find how and where the
    # smaller could be placed in the bigger one.
    #
    # E.g. you have 3 "+" lines, but 5 "-" lines. For now take the *first*
    # "+" line, and match it against "-" lines. The highest match ratio wins,
    # and we take it as the starting point for the intra-diff line-by-line
    # algorithm.
    if len(from_lines) < len(to_lines):
        base_line = from_lines[0].content
        matcher = partial(match_sequences, base_line)
        # We want that the smaller list fits completely into the bigger,
        # so we can't compare against *all* lines but have to stop
        # somewhere earlier.
        #
        # E.g. a = [1, 2, 3, 4, 5]; b = [1, 2].  Then, the candidates are
        #          [1, 2, 3, 4] otherwise b won't fit anymore
        #                   [1, 2]
        match_candidates = to_lines[:len(to_lines) - len(from_lines) + 1]
        to_lines = to_lines[find_best_slice(matcher, match_candidates)]
    else:
        base_line = to_lines[0].content
        matcher = partial(match_sequences, b=base_line)
        match_candidates = from_lines[:len(from_lines) - len(to_lines) + 1]
        from_lines = from_lines[find_best_slice(matcher, match_candidates)]

    if to_lines and from_lines:
        return intra_diff_line_by_line(from_lines, to_lines)
    else:
        return [], []


def find_best_slice(matcher, lines):
    # type: (Callable[[str], difflib.SequenceMatcher], List[HunkLine]) -> slice
    scores = []
    for n, line in enumerate(lines):
        matches = matcher(line.content)
        ratio = matches.ratio()
        if ratio >= 0.85:
            break
        scores.append((ratio, n))
    else:
        ratio, n = max(scores)
        if ratio < 0.75:
            return slice(0)

    return slice(n, None)


def intra_diff_line_by_line(from_lines, to_lines):
    # type: (List[HunkLine], List[HunkLine]) -> Tuple[List[Region], List[Region]]
    # Note: We have no guarantees here that from_lines and to_lines
    # have the same length, we use `zip` currently which produces
    # iterables of the shortest length of both!
    from_regions = []
    to_regions = []

    for from_line, to_line in zip(from_lines, to_lines):  # zip! see comment above
        # Compare without the leading mode char using ".content", but
        # also dedent both lines because leading common spaces will produce
        # higher ratios and produce slightly more ugly diffs.
        indentation = min(
            line_indentation(from_line.content),
            line_indentation(to_line.content)
        )
        a_input, b_input = from_line.content[indentation:], to_line.content[indentation:]
        matches = match_sequences(a_input, b_input)
        if matches.ratio() < 0.5:
            # We just continue, so it is possible that for a given chunk
            # *some* lines have markers, others not.
            # A different implementation could be: if *any* line within a hunk
            # is really low, like here 0.5, drop the hunk altogether.
            continue

        # Use tokenize strategy when there are "nearby" or fragmented splits
        # because it produces more calm output.
        if is_fragmented_match(matches):
            a_input = tokenize_string(a_input)  # type: ignore
            b_input = tokenize_string(b_input)  # type: ignore
            matches = match_sequences(a_input, b_input)

        a_offset = from_line.a + 1 + indentation
        b_offset = to_line.a + 1 + indentation
        from_offsets = tuple(accumulate(map(len, a_input), initial=a_offset))
        to_offsets = tuple(accumulate(map(len, b_input), initial=b_offset))
        for op, a_start, a_end, b_start, b_end in matches.get_opcodes():
            if op == 'equal':
                continue

            if a_start != a_end:
                from_regions.append(Region(from_offsets[a_start], from_offsets[a_end]))

            if b_start != b_end:
                to_regions.append(Region(to_offsets[b_start], to_offsets[b_end]))

    return from_regions, to_regions


boundary = re.compile(r'(\W)')
OPERATOR_CHARS = '=!<>'
COMPARISON_SENTINEL = object()


def tokenize_string(input_str):
    # type: (str) -> Sequence[str]
    # Usually a simple split on "\W" suffices, but here we join some
    # "operator" chars again.
    # About the "operator" chars: we want to treat e.g. "==" and "!="
    # as one token.  It is not important to treat e.g. "&&" as one token
    # because there is probably no "|&".  That is to say, if all chars
    # change we get a clean diff anyway, for the comparison operators
    # often only *one* of the characters changes ("<" to "<=" or "=="
    # to "!=") and then it looks better esp. with ligatures (!) if we
    # treat them as one token.
    return tuple(
        flatten(
            [''.join(chars)]
            if ch is COMPARISON_SENTINEL
            else list(chars)
            for ch, chars in groupby(
                filter(None, boundary.split(input_str)),
                key=lambda x: COMPARISON_SENTINEL if x in OPERATOR_CHARS else x
            )
        )
    )


def is_fragmented_match(matches):
    # type: (difflib.SequenceMatcher) -> bool
    a_input, b_input = matches.a, matches.b  # type: ignore  # stub bug?
    return any(
        (
            op == 'equal'
            and a_end - a_start == 1
            and not a_input[a_start:a_end] == '\n'
        )
        or (
            op == 'equal'
            and b_end - b_start == 1
            and not b_input[b_start:b_end] == '\n'
        )
        or (
            op != 'equal'
            and boundary.search(a_input[a_start:a_end] + b_input[b_start:b_end])
        )
        for op, a_start, a_end, b_start, b_end in matches.get_opcodes()
    )


class Region(sublime.Region):
    def __iter__(self):
        # type: () -> Iterator[int]
        return iter((self.a, self.b))

    def __add__(self, other):
        # type: (int) -> Region
        return self.transpose(other)

    def __sub__(self, other):
        # type: (int) -> Region
        return self.transpose(-other)

    def transpose(self, n):
        # type: (int) -> Region
        return Region(self.a + n, self.b + n)

    def as_slice(self):
        # type: () -> slice
        return slice(self.a, self.b)


class GsDiffToggleSetting(TextCommand):

    """
    Toggle view settings: `ignore_whitespace`.
    """

    def run(self, edit, setting):
        settings = self.view.settings()

        setting_str = "git_savvy.diff_view.{}".format(setting)
        current_mode = settings.get(setting_str)
        next_mode = not current_mode
        settings.set(setting_str, next_mode)
        self.view.window().status_message("{} is now {}".format(setting, next_mode))

        self.view.run_command("gs_diff_refresh")


class GsDiffCycleWordDiff(TextCommand):

    """
    Cycle through different word diff patterns.
    """

    def run(self, edit):
        settings = self.view.settings()

        setting_str = "git_savvy.diff_view.{}".format('show_word_diff')
        current_mode = settings.get(setting_str)
        next_mode = (current_mode + 1) % len(WORD_DIFF_PATTERNS)
        settings.set(setting_str, next_mode)

        self.view.run_command("gs_diff_refresh")


class GsDiffToggleCachedMode(TextCommand):

    """
    Toggle `in_cached_mode` or flip `base` with `target`.
    """

    # NOTE: MUST NOT be async, otherwise `view.show` will not update the view 100%!
    def run(self, edit):
        settings = self.view.settings()

        base_commit = settings.get("git_savvy.diff_view.base_commit")
        target_commit = settings.get("git_savvy.diff_view.target_commit")
        if base_commit and target_commit:
            settings.set("git_savvy.diff_view.base_commit", target_commit)
            settings.set("git_savvy.diff_view.target_commit", base_commit)
            self.view.run_command("gs_diff_refresh")
            return

        last_cursors = settings.get('git_savvy.diff_view.last_cursors') or []
        settings.set('git_savvy.diff_view.last_cursors', pickle_sel(self.view.sel()))

        setting_str = "git_savvy.diff_view.{}".format('in_cached_mode')
        current_mode = settings.get(setting_str)
        next_mode = not current_mode
        settings.set(setting_str, next_mode)
        self.view.window().status_message(
            "Showing {} changes".format("staged" if next_mode else "unstaged")
        )

        self.view.run_command("gs_diff_refresh")

        just_hunked = self.view.settings().get("git_savvy.diff_view.just_hunked")
        # Check for `last_cursors` as well bc it is only falsy on the *first*
        # switch. T.i. if the user hunked and then switches to see what will be
        # actually comitted, the view starts at the top. Later, the view will
        # show the last added hunk.
        if just_hunked and last_cursors:
            self.view.settings().set("git_savvy.diff_view.just_hunked", "")
            region = find_hunk_in_view(self.view, just_hunked)
            if region:
                set_and_show_cursor(self.view, region.a)
                return

        if last_cursors:
            # The 'flipping' between the two states should be as fast as possible and
            # without visual clutter.
            with no_animations():
                set_and_show_cursor(self.view, unpickle_sel(last_cursors))


class GsDiffZoom(TextCommand):
    """
    Update the number of context lines the diff shows by given `amount`
    and refresh the view.
    """
    def run(self, edit, amount):
        # type: (sublime.Edit, int) -> None
        settings = self.view.settings()
        current = settings.get('git_savvy.diff_view.context_lines')
        next = max(current + amount, 0)
        settings.set('git_savvy.diff_view.context_lines', next)

        # Getting a meaningful cursor after 'zooming' is the tricky part
        # here. We first extract all hunks under the cursors *verbatim*.
        diff = SplittedDiff.from_view(self.view)
        cur_hunks = [
            header.text + hunk.text
            for header, hunk in filter_(diff.head_and_hunk_for_pt(s.a) for s in self.view.sel())
        ]

        self.view.run_command("gs_diff_refresh")

        # Now, we fuzzy search the new view content for the old hunks.
        cursors = {
            region.a
            for region in (
                filter_(find_hunk_in_view(self.view, hunk) for hunk in cur_hunks)
            )
        }
        if cursors:
            set_and_show_cursor(self.view, cursors)


class GsDiffFocusEventListener(EventListener):

    """
    If the current view is a diff view, refresh the view with latest tree status
    when the view regains focus.
    """

    def on_activated_async(self, view):
        if view.settings().get("git_savvy.diff_view") is True:
            view.run_command("gs_diff_refresh", {"sync": False})


class GsDiffStageOrResetHunkCommand(TextCommand, GitCommand):

    """
    Depending on whether the user is in cached mode and what action
    the user took, either 1) stage, 2) unstage, or 3) reset the
    hunk under the user's cursor(s).
    """

    # NOTE: The whole command (including the view refresh) must be blocking otherwise
    # the view and the repo state get out of sync and e.g. hitting 'h' very fast will
    # result in errors.

    def run(self, edit, reset=False):
        ignore_whitespace = self.view.settings().get("git_savvy.diff_view.ignore_whitespace")
        show_word_diff = self.view.settings().get("git_savvy.diff_view.show_word_diff")
        if ignore_whitespace or show_word_diff:
            sublime.error_message("You have to be in a clean diff to stage.")
            return None

        # Filter out any cursors that are larger than a single point.
        cursor_pts = tuple(cursor.a for cursor in self.view.sel() if cursor.a == cursor.b)
        diff = SplittedDiff.from_view(self.view)

        patches = unique(flatten(filter_(diff.head_and_hunk_for_pt(pt) for pt in cursor_pts)))
        patch = ''.join(part.text for part in patches)

        if patch:
            self.apply_patch(patch, cursor_pts, reset)
        else:
            window = self.view.window()
            if window:
                window.status_message('Not within a hunk')

    def apply_patch(self, patch, pts, reset):
        in_cached_mode = self.view.settings().get("git_savvy.diff_view.in_cached_mode")
        context_lines = self.view.settings().get('git_savvy.diff_view.context_lines')

        # The three argument combinations below result from the following
        # three scenarios:
        #
        # 1) The user is in non-cached mode and wants to stage a hunk, so
        #    do NOT apply the patch in reverse, but do apply it only against
        #    the cached/indexed file (not the working tree).
        # 2) The user is in non-cached mode and wants to undo a line/hunk, so
        #    DO apply the patch in reverse, and do apply it both against the
        #    index and the working tree.
        # 3) The user is in cached mode and wants to undo a line hunk, so DO
        #    apply the patch in reverse, but only apply it against the cached/
        #    indexed file.
        #
        # NOTE: When in cached mode, no action will be taken when the user
        #       presses SUPER-BACKSPACE.

        args = (
            "apply",
            "-R" if (reset or in_cached_mode) else None,
            "--cached" if (in_cached_mode or not reset) else None,
            "--unidiff-zero" if context_lines == 0 else None,
            "-",
        )
        self.git(
            *args,
            stdin=patch
        )

        history = self.view.settings().get("git_savvy.diff_view.history")
        history.append((args, patch, pts, in_cached_mode))
        self.view.settings().set("git_savvy.diff_view.history", history)
        self.view.settings().set("git_savvy.diff_view.just_hunked", patch)

        self.view.run_command("gs_diff_refresh")


class GsDiffOpenFileAtHunkCommand(TextCommand, GitCommand):

    """
    For each cursor in the view, identify the hunk in which the cursor lies,
    and open the file at that hunk in a separate view.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        # Filter out any cursors that are larger than a single point.
        cursor_pts = tuple(cursor.a for cursor in self.view.sel() if cursor.a == cursor.b)

        def first_per_file(items):
            # type: (Iterator[Tuple[str, int, int]]) -> Iterator[Tuple[str, int, int]]
            seen = set()  # type: Set[str]
            for item in items:
                filename, _, _ = item
                if filename not in seen:
                    seen.add(filename)
                    yield item

        word_diff_mode = bool(self.view.settings().get('git_savvy.diff_view.show_word_diff'))
        diff = SplittedDiff.from_view(self.view)
        algo = (
            self.jump_position_to_file_for_word_diff_mode
            if word_diff_mode
            else self.jump_position_to_file
        )
        jump_positions = filter_(algo(diff, pt) for pt in cursor_pts)
        for jp in first_per_file(jump_positions):
            self.load_file_at_line(*jp)

    def load_file_at_line(self, filename, row, col):
        # type: (str, int, int) -> None
        """
        Show file at target commit if `git_savvy.diff_view.target_commit` is non-empty.
        Otherwise, open the file directly.
        """
        target_commit = self.view.settings().get("git_savvy.diff_view.target_commit")
        full_path = os.path.join(self.repo_path, filename)
        window = self.view.window()
        if not window:
            return

        if target_commit:
            window.run_command("gs_show_file_at_commit", {
                "commit_hash": target_commit,
                "filepath": full_path,
                "lineno": row,
            })
        else:
            window.open_file(
                "{file}:{row}:{col}".format(file=full_path, row=row, col=col),
                sublime.ENCODED_POSITION
            )

    def jump_position_to_file(self, diff, pt):
        # type: (SplittedDiff, int) -> Optional[Tuple[str, int, int]]
        head_and_hunk = diff.head_and_hunk_for_pt(pt)
        if not head_and_hunk:
            return None

        view = self.view
        header, hunk = head_and_hunk

        rowcol = real_rowcol_in_hunk(hunk, relative_rowcol_in_hunk(view, hunk, pt))
        if not rowcol:
            return None

        row, col = rowcol

        filename = header.b_filename()
        if not filename:
            return None

        return filename, row, col

    def jump_position_to_file_for_word_diff_mode(self, diff, pt):
        # type: (SplittedDiff, int) -> Optional[Tuple[str, int, int]]
        head_and_hunk = diff.head_and_hunk_for_pt(pt)
        if not head_and_hunk:
            return None

        view = self.view
        header, hunk = head_and_hunk
        content_start = hunk.content().a

        # Select all "deletion" regions in the hunk up to the cursor (pt)
        removed_regions_before_pt = [
            # In case the cursor is *in* a region, shorten it up to
            # the cursor.
            sublime.Region(region.begin(), min(region.end(), pt))
            for region in view.get_regions('git-savvy-removed-bold')
            if content_start <= region.begin() < pt
        ]

        # Count all completely removed lines, but exclude lines
        # if the cursor is exactly at the end-of-line char.
        removed_lines_before_pt = sum(
            region == view.line(region.begin()) and region.end() != pt
            for region in removed_regions_before_pt
        )
        line_start = view.line(pt).begin()
        removed_chars_before_pt = sum(
            region.size()
            for region in removed_regions_before_pt
            if line_start <= region.begin() < pt
        )

        # Compute the *relative* row in that hunk
        head_row, _ = view.rowcol(content_start)
        pt_row, col = view.rowcol(pt)
        rel_row = pt_row - head_row
        # If the cursor is in the hunk header, assume instead it is
        # at `(0, 0)` position in the hunk content.
        if rel_row < 0:
            rel_row, col = 0, 0

        # Extract the starting line at "b" encoded in the hunk header t.i. for
        # "@@ -685,8 +686,14 @@ ..." extract the "686".
        b = hunk.header().b_line_start()
        if b is None:
            return None
        row = b + rel_row

        filename = header.b_filename()
        if not filename:
            return None

        row = row - removed_lines_before_pt
        col = col + 1 - removed_chars_before_pt
        return filename, row, col


def relative_rowcol_in_hunk(view, hunk, pt):
    # type: (sublime.View, Hunk, Point) -> RowCol
    """Return rowcol of given pt relative to hunk start"""
    head_row, _ = view.rowcol(hunk.a)
    pt_row, col = view.rowcol(pt)
    # If `col=0` the user is on the meta char (e.g. '+- ') which is not
    # present in the source. We pin `col` to 1 because the target API
    # `open_file` expects 1-based row, col offsets.
    return pt_row - head_row, max(col, 1)


def real_rowcol_in_hunk(hunk, relative_rowcol):
    # type: (Hunk, RowCol) -> Optional[RowCol]
    """Translate relative to absolute row, col pair"""
    hunk_lines = counted_lines(hunk)
    if not hunk_lines:
        return None

    row_in_hunk, col = relative_rowcol

    # If the user is on the header line ('@@ ..') pretend to be on the
    # first visible line with some content instead.
    if row_in_hunk == 0:
        row_in_hunk = next(
            (
                index
                for index, line in enumerate(hunk_lines, 1)
                if line.mode in ('+', ' ') and line.text.strip()
            ),
            1
        )
        col = 1

    line = hunk_lines[row_in_hunk - 1]

    # Happy path since the user is on a present line
    if line.mode != '-':
        return line.b, col

    # The user is on a deleted line ('-') we cannot jump to. If possible,
    # select the next guaranteed to be available line
    for next_line in hunk_lines[row_in_hunk:]:
        if next_line.mode == '+':
            return next_line.b, min(col, len(next_line.text) + 1)
        elif next_line.mode == ' ':
            # If we only have a contextual line, choose this or the
            # previous line, pretty arbitrary, depending on the
            # indentation.
            next_lines_indentation = line_indentation(next_line.text)
            if next_lines_indentation == line_indentation(line.text):
                return next_line.b, next_lines_indentation + 1
            else:
                return max(1, line.b - 1), 1
    else:
        return line.b, 1


def counted_lines(hunk):
    # type: (Hunk) -> Optional[List[HunkLineWithB]]
    """Split a hunk into (first char, line content, row) tuples

    Note that rows point to available rows on the b-side.
    """
    b = hunk.header().b_line_start()
    if b is None:
        return None
    return list(_recount_lines(hunk.content().text.splitlines(), b))


def _recount_lines(lines, b):
    # type: (List[str], int) -> Iterator[HunkLineWithB]

    # Be aware that we only consider the b-line numbers, and that we
    # always yield a b value, even for deleted lines.
    for line in lines:
        first_char, tail = line[0], line[1:]
        yield HunkLineWithB(first_char, tail, b)

        if first_char != '-':
            b += 1


def line_indentation(line):
    # type: (str) -> int
    return len(line) - len(line.lstrip())


class GsDiffNavigateCommand(GsNavigate):

    """
    Travel between hunks. It is also used by show_commit_view.
    """

    offset = 0

    def get_available_regions(self):
        return [self.view.line(region) for region in
                self.view.find_by_selector("meta.diff.range.unified")]


class GsDiffUndo(TextCommand, GitCommand):

    """
    Undo the last action taken in the diff view, if possible.
    """

    # NOTE: MUST NOT be async, otherwise `view.show` will not update the view 100%!
    def run(self, edit):
        history = self.view.settings().get("git_savvy.diff_view.history")
        if not history:
            window = self.view.window()
            if window:
                window.status_message("Undo stack is empty")
            return

        args, stdin, cursors, in_cached_mode = history.pop()
        # Toggle the `--reverse` flag.
        args[1] = "-R" if not args[1] else None

        self.git(*args, stdin=stdin)
        self.view.settings().set("git_savvy.diff_view.history", history)
        self.view.settings().set("git_savvy.diff_view.just_hunked", stdin)

        self.view.run_command("gs_diff_refresh")

        # The cursor is only applicable if we're still in the same cache/stage mode
        if self.view.settings().get("git_savvy.diff_view.in_cached_mode") == in_cached_mode:
            set_and_show_cursor(self.view, cursors)


# ---  TYPES  --- #


if MYPY:
    SplittedDiffBase = NamedTuple(
        'SplittedDiff', [('headers', Tuple['Header', ...]), ('hunks', Tuple['Hunk', ...])]
    )
    TextRangeBase = NamedTuple('TextRange', [('text', str), ('a', int), ('b', int)])
    HunkLineWithB = NamedTuple('HunkLineWithB', [('mode', str), ('text', str), ('b', int)])
    TTextRange = TypeVar('TTextRange', bound='TextRange')
else:
    SplittedDiffBase = namedtuple('SplittedDiff', 'headers hunks')
    TextRangeBase = namedtuple('TextRange', 'text a b')
    HunkLineWithB = namedtuple('HunkLineWithB', 'mode text b')


class SplittedDiff(SplittedDiffBase):
    @classmethod
    def from_string(cls, text):
        # type: (str) -> SplittedDiff
        headers = [
            (match.start(), match.end())
            for match in re.finditer(r"^diff.*\n(?:.*\n)+?(?=diff|@@)", text, re.M)
        ]
        header_starts, header_ends = zip(*headers) if headers else ([], [])
        hunk_starts = tuple(match.start() for match in re.finditer("^@@", text, re.M))
        hunk_ends = tuple(sorted(
            # Hunks end when a diff starts, except for empty diffs.
            (set(header_starts[1:]) - set(header_ends)) |
            # Hunks end when the next hunk starts, except for hunks
            # immediately following diff headers.
            (set(hunk_starts) - set(header_ends)) |
            # The last hunk ends at the end of the file.
            # It should include the last line (`+ 1`).
            set((len(text) + 1, ))
        ))
        return cls(
            tuple(Header(text[a:b], a, b) for a, b in zip(header_starts, header_ends)),
            tuple(Hunk(text[a:b], a, b) for a, b in zip(hunk_starts, hunk_ends)),
        )

    @classmethod
    def from_view(cls, view):
        # type: (sublime.View) -> SplittedDiff
        return cls.from_string(view.substr(sublime.Region(0, view.size())))

    def head_and_hunk_for_pt(self, pt):
        # type: (int) -> Optional[Tuple[Header, Hunk]]
        for hunk in self.hunks:
            if hunk.a <= pt < hunk.b:
                break
        else:
            return None

        return self.head_for_hunk(hunk), hunk

    def head_for_hunk(self, hunk):
        # type: (Hunk) -> Header
        return max(
            (header for header in self.headers if header.a < hunk.a),
            key=lambda h: h.a
        )


HEADER_TO_FILE_RE = re.compile(r'\+\+\+ b/(.+)$')
HUNKS_LINES_RE = re.compile(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? ')


class TextRange(TextRangeBase):
    def __new__(cls, text, a=0, b=None):
        # type: (Type[TTextRange], str, int, int) -> TTextRange
        if b is None:
            b = len(text)
        return super().__new__(cls, text, a, b)

    _line_factory = None  # type: Type[TextRange]

    @property
    def region(self):
        # type: () -> Region
        return Region(self.a, self.b)

    def lines(self):
        # type: () -> List[TextRange]
        _factory = self._line_factory or TextRange
        lines = self.text.splitlines(keepends=True)
        return [
            _factory(line, *a_b)
            for line, a_b in zip(lines, pairwise(accumulate(map(len, lines), initial=self.a)))
        ]

    def substr(self, region):
        # type: (Region) -> TextRange
        text = self.text[region.as_slice()]
        return TextRange(text, *(region + self.a))

    def split_region_by_newlines(self, region):
        # type: (Region) -> List[Region]
        return [line.region for line in self.substr(region).lines()]


class Header(TextRange):
    def b_filename(self):
        # type: () -> Optional[str]
        match = HEADER_TO_FILE_RE.search(self.text)
        if not match:
            return None

        return match.group(1)


class Hunk(TextRange):
    def header(self):
        # type: () -> HunkHeader
        content_start = self.text.index('\n') + 1
        return HunkHeader(self.text[:content_start], self.a, self.a + content_start)

    def content(self):
        # type: () -> HunkContent
        content_start = self.text.index('\n') + 1
        return HunkContent(self.text[content_start:], self.a + content_start, self.b)


class HunkHeader(TextRange):
    def b_line_start(self):
        # type: () -> Optional[int]
        """Extract the starting line at "b" encoded in the hunk header

        T.i. for "@@ -685,8 +686,14 @@ ..." extract the "686".
        """
        match = HUNKS_LINES_RE.search(self.text)
        if not match:
            return None

        return int(match.group(2))


class HunkLine(TextRange):
    @property
    def mode(self):
        # type: () -> str
        return self.text[0]

    @property
    def content(self):
        # type: () -> str
        return self.text[1:]

    def is_context(self):
        return self.mode.strip() == ''

    def is_no_newline_marker(self):
        return self.text.strip() == "\\ No newline at end of file"


class HunkContent(TextRange):
    _line_factory = HunkLine

    if MYPY:
        def lines(self):  # type: ignore
            # type: () -> List[HunkLine]
            return super().lines()  # type: ignore


def find_hunk_in_view(view, patch):
    # type: (sublime.View, str) -> Optional[sublime.Region]
    """Given a patch, search for its first hunk in the view

    Returns the region of the first line of the hunk (the one starting
    with '@@ ...'), if any.
    """
    diff = SplittedDiff.from_string(patch)
    try:
        hunk = diff.hunks[0]
    except IndexError:
        return None

    return (
        view.find(hunk.header().text, 0, sublime.LITERAL)
        or fuzzy_search_hunk_content_in_view(view, hunk.content().text.splitlines())
    )


def fuzzy_search_hunk_content_in_view(view, lines):
    # type: (sublime.View, List[str]) -> Optional[sublime.Region]
    """Fuzzy search the hunk content in the view

    Note that hunk content does not include the starting line, the one
    starting with '@@ ...', anymore.

    The fuzzy strategy here is to search for the hunk or parts of it
    by reducing the contextual lines symmetrically.

    Returns the region of the starting line of the found hunk, if any.
    """
    for hunk_content in shrink_list_sym(lines):
        region = view.find('\n'.join(hunk_content), 0, sublime.LITERAL)
        if region:
            diff = SplittedDiff.from_view(view)
            head_and_hunk = diff.head_and_hunk_for_pt(region.a)
            if head_and_hunk:
                _, hunk = head_and_hunk
                hunk_header = hunk.header()
                return sublime.Region(hunk_header.a, hunk_header.b)
            break
    return None


def shrink_list_sym(list):
    # type: (List[T]) -> Iterator[List[T]]
    while list:
        yield list
        list = list[1:-1]


def accumulate(iterable, initial):
    # type: (Iterable[int], int) -> Iterable[int]
    if initial is None:
        return accumulate_(iterable)
    else:
        return accumulate_(chain([initial], iterable))


def pairwise(iterable):
    # type: (Iterable[T]) -> Iterable[Tuple[T, T]]
    "s -> (s0,s1), (s1,s2), (s2, s3), ..."
    a, b = tee(iterable)
    next(b, None)
    return zip(a, b)


def pickle_sel(sel):
    return [(s.a, s.b) for s in sel]


def unpickle_sel(pickled_sel):
    return [sublime.Region(a, b) for a, b in pickled_sel]


def unique(items):
    # type: (Iterable[T]) -> List[T]
    """Remove duplicate entries but remain sorted/ordered."""
    rv = []  # type: List[T]
    for item in items:
        if item not in rv:
            rv.append(item)
    return rv


def set_and_show_cursor(view, cursors):
    sel = view.sel()
    sel.clear()
    try:
        it = iter(cursors)
    except TypeError:
        sel.add(cursors)
    else:
        for c in it:
            sel.add(c)

    view.show(sel)


@contextmanager
def no_animations():
    pref = sublime.load_settings("Preferences.sublime-settings")
    current = pref.get("animation_enabled")
    pref.set("animation_enabled", False)
    try:
        yield
    finally:
        pref.set("animation_enabled", current)
