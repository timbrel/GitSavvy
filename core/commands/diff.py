"""
Implements a special view to visualize and stage pieces of a project's
current diff.
"""

from collections import defaultdict, namedtuple
from contextlib import contextmanager
from itertools import chain, count, groupby, takewhile
import os

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from . import intra_line_colorizer
from . import stage_hunk
from .navigate import GsNavigate
from ..fns import filter_, flatten, unique
from ..parse_diff import SplittedDiff
from ..git_command import GitCommand
from ..runtime import enqueue_on_ui, enqueue_on_worker
from ..utils import flash, focus_view, line_indentation
from ..view import replace_view_content, place_view, row_offset, Position
from ...common import util


__all__ = (
    "gs_diff",
    "gs_diff_refresh",
    "gs_diff_toggle_setting",
    "gs_diff_toggle_cached_mode",
    "gs_diff_zoom",
    "gs_diff_stage_or_reset_hunk",
    "gs_diff_open_file_at_hunk",
    "gs_diff_navigate",
    "gs_diff_undo",
    "GsDiffFocusEventListener",
)


MYPY = False
if MYPY:
    from typing import (
        Callable, Dict, Iterable, Iterator, List, NamedTuple, Optional, Set,
        Tuple, TypeVar
    )
    from ..parse_diff import FileHeader, Hunk, HunkLine, TextRange
    from ..types import LineNo, ColNo

    T = TypeVar('T')
    Point = int
    LineCol = Tuple[LineNo, ColNo]
    HunkLineWithB = NamedTuple('HunkLineWithB', [('line', 'HunkLine'), ('b', LineNo)])
else:
    HunkLineWithB = namedtuple('HunkLineWithB', 'line b')


DIFF_TITLE = "DIFF: {}"
DIFF_CACHED_TITLE = "DIFF: {} (staged)"
DECODE_ERROR_MESSAGE = """
-- Can't decode diff output. --

You may have checked in binary data git doesn't detect, or not UTF-8
encoded files. In the latter case use the "fallback_encoding" setting,
in the former you may want to edit the `.gitattributes` file.

Note, however, that diffs with *mixed* encodings are not supported.
"""


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


def compute_identifier_for_view(view):
    # type: (sublime.View) -> Optional[Tuple]
    settings = view.settings()
    return (
        settings.get('git_savvy.repo_path'),
        settings.get('git_savvy.file_path'),
        settings.get('git_savvy.diff_view.base_commit'),
        settings.get('git_savvy.diff_view.target_commit')
    ) if settings.get('git_savvy.diff_view') else None


class gs_diff(WindowCommand, GitCommand):

    """
    Create a new view to display the difference of `target_commit`
    against `base_commit`. If `target_commit` is None, compare
    working directory with `base_commit`.  If `in_cached_mode` is set,
    display a diff of the Git index. Set `disable_stage` to True to
    disable Ctrl-Enter in the diff view.
    """

    def run(
        self,
        repo_path=None,
        file_path=None,
        in_cached_mode=None,  # type: Optional[bool]
        current_file=False,
        base_commit=None,
        target_commit=None,
        disable_stage=False,
        title=None,
        ignore_whitespace=False,
        context_lines=3
    ):
        # type: (...) -> None
        if repo_path is None:
            repo_path = self.repo_path
        assert repo_path
        if current_file:
            file_path = self.file_path or file_path

        active_view = self.window.active_view()
        this_id = (
            repo_path,
            file_path,
            base_commit,
            target_commit
        )
        for view in self.window.views():
            if compute_identifier_for_view(view) == this_id:
                settings = view.settings()
                if view == active_view and (
                    in_cached_mode is None
                    or settings.get("git_savvy.diff_view.in_cached_mode") == in_cached_mode
                ):
                    view.close()
                    return

                if in_cached_mode is not None:
                    settings.set("git_savvy.diff_view.in_cached_mode", in_cached_mode)
                focus_view(view)
                if active_view:
                    place_view(self.window, view, after=active_view)
                break

        else:
            diff_view = util.view.get_scratch_view(self, "diff", read_only=True)

            show_diffstat = self.savvy_settings.get("show_diffstat", True)
            settings = diff_view.settings()
            settings.set("git_savvy.repo_path", repo_path)
            settings.set("git_savvy.file_path", file_path)
            settings.set("git_savvy.diff_view.in_cached_mode", bool(in_cached_mode))
            settings.set("git_savvy.diff_view.ignore_whitespace", ignore_whitespace)
            settings.set("git_savvy.diff_view.context_lines", context_lines)
            settings.set("git_savvy.diff_view.base_commit", base_commit)
            settings.set("git_savvy.diff_view.target_commit", target_commit)
            settings.set("git_savvy.diff_view.show_diffstat", show_diffstat)
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

            diff_view.run_command("gs_handle_vintageous")
            # Assume diffing a single file is very fast and do it
            # sync because it looks better.
            diff_view.run_command("gs_diff_refresh", {"sync": bool(file_path)})


class gs_diff_refresh(TextCommand, GitCommand):
    """Refresh the diff view with the latest repo state."""

    def run(self, edit, sync=True):
        if sync:
            self.run_impl(sync)
        else:
            enqueue_on_worker(self.run_impl, sync)

    def run_impl(self, runs_on_ui_thread):
        view = self.view
        if not runs_on_ui_thread and not view.is_valid():
            return

        settings = view.settings()
        repo_path = settings.get("git_savvy.repo_path")
        file_path = settings.get("git_savvy.file_path")
        in_cached_mode = settings.get("git_savvy.diff_view.in_cached_mode")
        ignore_whitespace = settings.get("git_savvy.diff_view.ignore_whitespace")
        base_commit = settings.get("git_savvy.diff_view.base_commit")
        target_commit = settings.get("git_savvy.diff_view.target_commit")
        show_diffstat = settings.get("git_savvy.diff_view.show_diffstat")
        disable_stage = settings.get("git_savvy.diff_view.disable_stage")
        context_lines = settings.get('git_savvy.diff_view.context_lines')

        prelude = "\n"
        title = ["DIFF:"]
        if file_path:
            rel_file_path = os.path.relpath(file_path, repo_path)
            prelude += "  FILE: {}\n".format(rel_file_path)
            title += [os.path.basename(file_path)]
        elif not disable_stage:
            title += [os.path.basename(repo_path)]

        if disable_stage:
            if in_cached_mode:
                prelude += "  {}..INDEX\n".format(base_commit or target_commit)
                title += ["{}..INDEX".format(base_commit or target_commit)]
            else:
                if base_commit and target_commit:
                    prelude += "  {}..{}\n".format(base_commit, target_commit)
                    title += ["{}..{}".format(base_commit, target_commit)]
                elif base_commit and "..." in base_commit:
                    prelude += "  {}\n".format(base_commit)
                    title += [base_commit]
                else:
                    prelude += "  {}..WORKING DIR\n".format(base_commit or target_commit)
                    title += ["{}..WORKING DIR".format(base_commit or target_commit)]
        else:
            if in_cached_mode:
                prelude += "  STAGED CHANGES (Will commit)\n"
                title += ["(staged)"]
            else:
                prelude += "  UNSTAGED CHANGES\n"

        if ignore_whitespace:
            prelude += "  IGNORING WHITESPACE\n"

        prelude += "\n--\n"

        raw_diff = self.git(
            "diff",
            "--ignore-all-space" if ignore_whitespace else None,
            "--unified={}".format(context_lines) if context_lines is not None else None,
            "--stat" if show_diffstat else None,
            "--patch",
            "--no-color",
            "--cached" if in_cached_mode else None,
            base_commit,
            target_commit,
            "--",
            file_path,
            decode=False
        )

        try:
            diff = self.strict_decode(raw_diff)
        except UnicodeDecodeError:
            diff = DECODE_ERROR_MESSAGE
            diff += "\n-- Partially decoded output follows; � denotes decoding errors --\n\n"""
            diff += raw_diff.decode("utf-8", "replace")

        if settings.get("git_savvy.just_committed"):
            if diff:
                settings.set("git_savvy.just_committed", False)
            else:
                if in_cached_mode:
                    settings.set("git_savvy.diff_view.in_cached_mode", False)
                    view.run_command("gs_diff_refresh")
                else:
                    view.close()
                return

        has_content = view.find_by_selector("git-savvy.diff_view git-savvy.diff")
        draw = lambda: _draw(
            view,
            ' '.join(title),
            prelude,
            diff,
            navigate=not has_content
        )
        if runs_on_ui_thread:
            draw()
        else:
            enqueue_on_ui(draw)


def _draw(view, title, prelude, diff_text, navigate):
    # type: (sublime.View, str, str, str, bool) -> None
    view.set_name(title)
    text = prelude + diff_text
    replace_view_content(view, text)
    if navigate:
        view.run_command("gs_diff_navigate")

    intra_line_colorizer.annotate_intra_line_differences(view, diff_text, len(prelude))


class gs_diff_toggle_setting(TextCommand):

    """
    Toggle view settings: `ignore_whitespace`.
    """

    def run(self, edit, setting):
        settings = self.view.settings()

        setting_str = "git_savvy.diff_view.{}".format(setting)
        current_mode = settings.get(setting_str)
        next_mode = not current_mode
        settings.set(setting_str, next_mode)
        flash(self.view, "{} is now {}".format(setting, next_mode))

        self.view.run_command("gs_diff_refresh")


class gs_diff_toggle_cached_mode(TextCommand):

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

        if base_commit and "..." in base_commit:
            a, b = base_commit.split("...")
            settings.set("git_savvy.diff_view.base_commit", "{}...{}".format(b, a))
            self.view.run_command("gs_diff_refresh")
            return

        last_cursors = settings.get('git_savvy.diff_view.last_cursors') or []
        settings.set('git_savvy.diff_view.last_cursors', pickle_sel(self.view.sel()))

        setting_str = "git_savvy.diff_view.{}".format('in_cached_mode')
        current_mode = settings.get(setting_str)
        next_mode = not current_mode
        settings.set(setting_str, next_mode)
        flash(self.view, "Showing {} changes".format("staged" if next_mode else "unstaged"))

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


class gs_diff_zoom(TextCommand):
    """
    Update the number of context lines the diff shows by given `amount`
    and refresh the view.
    """
    def run(self, edit, amount):
        # type: (sublime.Edit, int) -> None
        settings = self.view.settings()
        current = settings.get('git_savvy.diff_view.context_lines')

        MINIMUM, DEFAULT, MIN_STEP_SIZE = 1, 3, 5
        step_size = max(abs(amount), MIN_STEP_SIZE)
        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
        if amount > 0:
            next_value = next(x for x in values if x > current)
        else:
            try:
                next_value = list(takewhile(lambda x: x < current, values))[-1]
            except IndexError:
                next_value = MINIMUM

        settings.set('git_savvy.diff_view.context_lines', next_value)

        # Getting a meaningful cursor after 'zooming' is the tricky part
        # here. We first extract all hunks under the cursors *verbatim*.
        diff = SplittedDiff.from_view(self.view)
        cur_hunks = []
        for s in self.view.sel():
            hunk = diff.hunk_for_pt(s.a)
            if hunk:
                head_line = diff.head_for_hunk(hunk).first_line()
                for line, line_id in compute_line_ids_for_hunk(hunk):
                    line_region = line.region()
                    # `line_region` spans the *full* line including the
                    # trailing newline char (if any).  Compare excluding
                    # `line_region.b` to not match a cursor at BOL
                    # position on the next line.
                    if line_region.a <= s.a < line_region.b:
                        cur_hunks.append((head_line, line_id, row_offset(self.view, s.a)))
                        break
                else:
                    # If the user is on the very last line of the view, create
                    # a fake line after that.
                    cur_hunks.append((
                        head_line,
                        LineId(line_id.a + 1, line_id.b + 1),
                        row_offset(self.view, s.a)
                    ))

        self.view.run_command("gs_diff_refresh")

        # Now, we fuzzy search the new view content for the old hunks.
        diff = SplittedDiff.from_view(self.view)
        cursors = set()
        scroll_offsets = []
        for head_line, line_id, offset in cur_hunks:
            region = find_line_in_diff(diff, head_line, line_id)
            if region:
                cursors.add(region.a)
                row, _ = self.view.rowcol(region.a)
                scroll_offsets.append((row, offset))

        if not cursors:
            return

        self.view.sel().clear()
        self.view.sel().add_all(list(cursors))

        row, offset = min(scroll_offsets, key=lambda row_offset: abs(row_offset[1]))
        vy = (row - offset) * self.view.line_height()
        vx, _ = self.view.viewport_position()
        self.view.set_viewport_position((vx, vy), animate=False)


if MYPY:
    LineId = NamedTuple("LineId", [("a", LineNo), ("b", LineNo)])
    HunkLineWithLineNumbers = Tuple[HunkLine, LineId]
    HunkLineWithLineId = Tuple[TextRange, LineId]
else:
    LineId = namedtuple("LineId", "a b")


def compute_line_ids_for_hunk(hunk):
    # type: (Hunk) -> Iterator[HunkLineWithLineId]
    # Use `safely_parse_metadata` to not throw on combined diffs.
    # In that case, the computed line numbers can only be used as identifiers,
    # really counting lines from a combined diff is not implemented here!
    metadata = hunk.header().safely_parse_metadata()
    (a_start, _), (b_start, _) = metadata[0], metadata[-1]
    yield hunk.header(), LineId(a_start - 1, b_start - 1)
    yield from __recount_lines(hunk.content().lines(), a_start, b_start)


def find_line_in_diff(diff, head_line, wanted_line_id):
    # type: (SplittedDiff, str, LineId) -> Optional[sublime.Region]
    header = next((h for h in diff.headers if h.first_line() == head_line), None)
    if header:
        for hunk in diff.hunks_for_head(header):
            for line, line_id in compute_line_ids_for_hunk(hunk):
                if line_id >= wanted_line_id:
                    return line.region()
    return None


class GsDiffFocusEventListener(EventListener):

    """
    If the current view is a diff view, refresh the view with latest tree status
    when the view regains focus.
    """

    def on_activated(self, view):
        settings = view.settings()
        if settings.get("git_savvy.ignore_next_activated_event"):
            settings.set("git_savvy.ignore_next_activated_event", False)
        elif settings.get("git_savvy.diff_view"):
            view.run_command("gs_diff_refresh", {"sync": False})


class gs_diff_stage_or_reset_hunk(TextCommand, GitCommand):

    """
    Depending on whether the user is in cached mode and what action
    the user took, either 1) stage, 2) unstage, or 3) reset the
    hunk under the user's cursor(s).
    """

    # NOTE: The whole command (including the view refresh) must be blocking otherwise
    # the view and the repo state get out of sync and e.g. hitting 'h' very fast will
    # result in errors.

    def run(self, edit, reset=False, whole_file=False):
        # type: (sublime.Edit, bool, bool) -> None
        ignore_whitespace = self.view.settings().get("git_savvy.diff_view.ignore_whitespace")
        if ignore_whitespace:
            sublime.error_message("Staging is not supported while ignoring [w]hitespace is on.")
            return None

        in_cached_mode = self.view.settings().get("git_savvy.diff_view.in_cached_mode")
        if in_cached_mode and reset:
            flash(self.view, "Can't discard staged changes.  Unstage first.")
            return None

        frozen_sel = [s for s in self.view.sel()]
        cursor_pts = [s.a for s in frozen_sel]
        diff = SplittedDiff.from_view(self.view)

        if whole_file or all(s.empty() for s in frozen_sel):
            if whole_file:
                hunks = filter_(map(diff.hunk_for_pt, cursor_pts))
                headers = unique(map(diff.head_for_hunk, hunks))
                patches = flatten(
                    chain([head], diff.hunks_for_head(head))
                    for head in headers
                )  # type: Iterable[TextRange]
            else:
                patches = unique(flatten(filter_(diff.head_and_hunk_for_pt(pt) for pt in cursor_pts)))
            patch = ''.join(part.text for part in patches)
            zero_diff = self.view.settings().get('git_savvy.diff_view.context_lines') == 0
        else:
            line_starts = selected_line_starts(self.view, frozen_sel)
            patch = compute_patch_for_sel(diff, line_starts, reset or in_cached_mode)
            zero_diff = True

        if patch:
            self.apply_patch(patch, cursor_pts, reset, zero_diff)
            first_cursor = self.view.sel()[0].begin()
            self.view.sel().clear()
            self.view.sel().add(first_cursor)
        else:
            flash(self.view, "Not within a hunk")

    def apply_patch(self, patch, pts, reset, zero_diff):
        # type: (str, List[int], bool, bool) -> None
        in_cached_mode = self.view.settings().get("git_savvy.diff_view.in_cached_mode")

        # ATT: Undo expects always the same args length and order!
        args = ["apply"]  # type: List[Optional[str]]
        if reset:
            args += ["-R", None]        # discard
        elif in_cached_mode:
            args += ["-R", "--cached"]  # unstage
        else:
            args += [None, "--cached"]  # stage

        if zero_diff:
            args += ["--unidiff-zero"]

        args += ["-"]
        self.git(*args, stdin=patch)

        history = self.view.settings().get("git_savvy.diff_view.history")
        history.append((args, patch, pts, in_cached_mode))
        self.view.settings().set("git_savvy.diff_view.history", history)
        self.view.settings().set("git_savvy.diff_view.just_hunked", patch)

        self.view.run_command("gs_diff_refresh")


def selected_line_starts(view, sel):
    # type: (sublime.View, List[sublime.Region]) -> Set[int]
    selected_lines = flatten(map(view.lines, sel))
    return set(line.a for line in selected_lines)


def chunkby(it, predicate):
    # type: (Iterable[T], Callable[[T], bool]) -> Iterator[List[T]]
    return (list(items) for selected, items in groupby(it, key=predicate) if selected)


def compute_patch_for_sel(diff, line_starts, reverse):
    # type: (SplittedDiff, Set[int], bool) -> str
    hunks = unique(filter_(diff.hunk_for_pt(pt) for pt in sorted(line_starts)))

    def not_context(line_ab):
        line, _ = line_ab
        return not line.is_context()

    def selected(line_ab):
        line, _ = line_ab
        return line.a in line_starts

    patches = defaultdict(list)  # type: Dict[FileHeader, List[stage_hunk.Hunk]]
    for hunk in hunks:
        header = diff.head_for_hunk(hunk)
        for chunk in chunkby(recount_lines(hunk), not_context):
            selected_lines = list(filter(selected, chunk))
            if selected_lines:
                patches[header].append(form_patch(selected_lines))

    whole_patch = "".join(
        stage_hunk.format_patch(header.text, hunks, reverse=reverse)
        for header, hunks in patches.items()
    )
    return whole_patch


def form_patch(lines):
    # type: (List[HunkLineWithLineNumbers]) -> stage_hunk.Hunk
    line, a_b = lines[0]
    a_start, b_start = a_b
    if lines[0][0].is_from_line() and lines[-1][0].is_to_line():
        b_start = next(a_b.b for line, a_b in lines if line.is_to_line())
    alen = sum(1 for line, a_b in lines if not line.is_to_line())
    blen = sum(1 for line, a_b in lines if not line.is_from_line())
    content = "".join(line.text for line, a_b in lines)
    return stage_hunk.Hunk(a_start, alen, b_start, blen, content)


MYPY = False
if MYPY:
    from typing import NamedTuple
    JumpTo = NamedTuple('JumpTo', [
        ('commit_hash', Optional[str]),
        ('filename', str),
        ('line', LineNo),
        ('col', ColNo)
    ])
else:
    from collections import namedtuple
    JumpTo = namedtuple('JumpTo', 'commit_hash filename line col')


class gs_diff_open_file_at_hunk(TextCommand, GitCommand):

    """
    For each cursor in the view, identify the hunk in which the cursor lies,
    and open the file at that hunk in a separate view.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None

        def first_per_file(items):
            # type: (Iterator[JumpTo]) -> Iterator[JumpTo]
            seen = set()  # type: Set[str]
            for item in items:
                if item.filename not in seen:
                    seen.add(item.filename)
                    yield item

        diff = SplittedDiff.from_view(self.view)
        jump_positions = list(first_per_file(filter_(
            jump_position_to_file(self.view, diff, s.begin())
            for s in self.view.sel()
        )))
        if not jump_positions:
            flash(self.view, "Not within a hunk")
        else:
            for jp in jump_positions:
                self.load_file_at_line(*jp)

    def load_file_at_line(self, commit_hash, filename, line, col):
        # type: (Optional[str], str, LineNo, ColNo) -> None
        """
        Show file at target commit if `git_savvy.diff_view.target_commit` is non-empty.
        Otherwise, open the file directly.
        """
        target_commit = commit_hash or self.view.settings().get("git_savvy.diff_view.target_commit")
        full_path = os.path.join(self.repo_path, filename)
        window = self.view.window()
        if not window:
            return

        if target_commit:
            window.run_command("gs_show_file_at_commit", {
                "commit_hash": target_commit,
                "filepath": full_path,
                "position": Position(line - 1, col - 1, None),
            })
        else:
            window.open_file(
                "{file}:{line}:{col}".format(file=full_path, line=line, col=col),
                sublime.ENCODED_POSITION
            )


def jump_position_to_file(view, diff, pt):
    # type: (sublime.View, SplittedDiff, int) -> Optional[JumpTo]
    head_and_hunk = diff.head_and_hunk_for_pt(pt)
    if not head_and_hunk:
        return None

    header, hunk = head_and_hunk

    linecol = real_linecol_in_hunk(hunk, *row_offset_and_col_in_hunk(view, hunk, pt))
    if not linecol:
        return None

    line, col = linecol

    filename = header.from_filename()
    if not filename:
        return None

    commit_header = diff.commit_for_hunk(hunk)
    commit_hash = commit_header.commit_hash() if commit_header else None
    return JumpTo(commit_hash, filename, line, col)


def row_offset_and_col_in_hunk(view, hunk, pt):
    # type: (sublime.View, Hunk, Point) -> Tuple[int, ColNo]
    """Return row offset of `pt` relative to hunk start and its column

    Note that the column is already 1-based t.i. a `ColNo`
    """
    head_row, _ = view.rowcol(hunk.a)
    pt_row, col = view.rowcol(pt)
    # We want to map columns in a diff output to columns in real files.
    # Strip "line-mode" characters at the start of the line.
    # Since `col` as returned by `rowcol` is 0-based (and we want 1-based
    # line and columns here) account for that as well.
    # Often the user will be on the first char of the line, t.i. within
    # the "line-mode" section. Return `1` in that case.
    return pt_row - head_row, max(col - hunk.mode_len() + 1, 1)


def real_linecol_in_hunk(hunk, row_offset, col):
    # type: (Hunk, int, ColNo) -> Optional[LineCol]
    """Translate relative to absolute line, col pair"""
    hunk_lines = list(recount_lines_for_jump_to_file(hunk))

    # If the user is on the header line ('@@ ..') pretend to be on the
    # first visible line with some content instead.
    if row_offset == 0:
        row_offset = next(
            (
                index
                for index, (line, _) in enumerate(hunk_lines, 1)
                if not line.is_from_line() and line.content.strip()
            ),
            1
        )
        col = 1

    line, b = hunk_lines[row_offset - 1]

    # Happy path since the user is on a present line
    if not line.is_from_line():
        return b, col

    # The user is on a deleted line ('-') we cannot jump to. If possible,
    # select the next guaranteed to be available line
    for next_line, next_b in hunk_lines[row_offset:]:
        if next_line.is_to_line():
            return next_b, min(col, len(next_line.content) + 1)
        elif next_line.is_context():
            # If we only have a contextual line, choose this or the
            # previous line, pretty arbitrary, depending on the
            # indentation.
            next_lines_indentation = line_indentation(next_line.content)
            if next_lines_indentation == line_indentation(line.content):
                return next_b, next_lines_indentation + 1
            else:
                return max(1, b - 1), 1
    else:
        return b, 1


def recount_lines_for_jump_to_file(hunk):
    # type: (Hunk) -> Iterator[HunkLineWithB]
    """Recount lines for the jump-to-file feature.

    Only computes b values and handles deletions weird, e.g. for

    ```
        @@ -383,4 +383,3 @@ class gs_diff_zoom(TextCommand):
             step_size = max(abs(amount), MIN_STEP_SIZE)
    -        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
    -        if amount > 0:
    +        zif amount > 0:
             next_value = next(x for x in values if x > current)

    ```

    yields `(383, 384, 384, 384, 385)`.  That's visually appealing
    though.

    """
    b_start = hunk.header().to_line_start()
    for line in hunk.content().lines():
        yield HunkLineWithB(line, b_start)
        if not line.is_from_line():
            b_start += 1


def recount_lines(hunk):
    # type: (Hunk) -> Iterator[HunkLineWithLineNumbers]
    a_start, _, b_start, _ = hunk.header().parse()
    yield from __recount_lines(hunk.content().lines(), a_start, b_start)


def __recount_lines(hunk_lines, a_start, b_start):
    # type: (Iterable[HunkLine], int, int) -> Iterator[HunkLineWithLineNumbers]
    it = iter(hunk_lines)
    line = next(it)
    yield line, LineId(a_start, b_start)
    for line in it:
        if line.is_context():
            a_start += 1
            b_start += 1
        elif line.is_from_line():
            a_start += 1
        elif line.is_to_line():
            b_start += 1
        yield line, LineId(a_start, b_start)


class gs_diff_navigate(GsNavigate):

    """
    Travel between hunks. It is also used by show_commit_view.
    """

    offset = 0

    def get_available_regions(self):
        return self.view.find_by_selector("meta.diff.range.unified, meta.commit-info.header")


class gs_diff_undo(TextCommand, GitCommand):

    """
    Undo the last action taken in the diff view, if possible.
    """

    # NOTE: MUST NOT be async, otherwise `view.show` will not update the view 100%!
    def run(self, edit):
        history = self.view.settings().get("git_savvy.diff_view.history")
        if not history:
            flash(self.view, "Undo stack is empty")
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


def pickle_sel(sel):
    return [(s.a, s.b) for s in sel]


def unpickle_sel(pickled_sel):
    return [sublime.Region(a, b) for a, b in pickled_sel]


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
