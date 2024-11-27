from itertools import groupby, takewhile
import os
from contextlib import contextmanager

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from . import diff
from . import show_file_at_commit
from .navigate import GsNavigate
from ..git_command import GitCommand
from ..parse_diff import SplittedDiff, UnsupportedCombinedDiff
from ..runtime import enqueue_on_ui, enqueue_on_worker
from ..utils import flash, focus_view
from ..view import (
    apply_position, capture_cur_position, other_visible_views, place_view,
    replace_view_content, y_offset, Position)
from ...common import util


__all__ = (
    "gs_inline_diff",
    "gs_inline_diff_open",
    "gs_inline_diff_refresh",
    "gs_inline_diff_toggle_side",
    "gs_inline_diff_toggle_cached_mode",
    "gs_inline_diff_stage_or_reset_line",
    "gs_inline_diff_stage_or_reset_hunk",
    "gs_inline_diff_previous_commit",
    "gs_inline_diff_next_commit",
    "gs_inline_diff_open_file",
    "gs_inline_diff_open_file_at_hunk",
    "gs_inline_diff_open_graph_context",
    "gs_inline_diff_navigate_hunk",
    "gs_inline_diff_undo",
    "GsInlineDiffFocusEventListener",
)


from typing import Dict, Iterable, List, Literal, NamedTuple, Optional, Tuple
from ..types import LineNo, ColNo, Row
from GitSavvy.common.util.parse_diff import Hunk as InlineDiff_Hunk


class HunkReference(NamedTuple):
    section_start: Row
    section_end: Row
    hunk: InlineDiff_Hunk
    line_types: List[str]
    lines: List[str]  # sic! => "line_contents"


DECODE_ERROR_MESSAGE = (
    "Can't decode diff output.  "
    "You may have checked in binary data git doesn't detect, or not UTF-8 "
    "encoded files.  In the latter case use the 'fallback_encoding' setting, "
    "in the former you may want to edit the `.gitattributes` file.  "
    "Note, however, that diffs with *mixed* encodings are not supported."
)

INLINE_DIFF_TITLE = "INLINE: {}"
INLINE_DIFF_CACHED_TITLE = "INLINE: {}, STAGE"

DIFF_HEADER = """diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
"""

diff_view_hunks = {}  # type: Dict[sublime.ViewId, List[HunkReference]]
active_on_activated = True


def translate_row_to_inline_diff(diff_view, row):
    # type: (sublime.View, Row) -> Row
    hunks = diff_view_hunks[diff_view.id()]
    return row + count_deleted_lines_before_line(hunks, row + 1)


def count_deleted_lines_before_line(hunks, line):
    # type: (Iterable[HunkReference], LineNo) -> int
    return sum(
        hunk.head_length
        for hunk in takewhile(
            lambda hunk: line >= real_saved_start(hunk),
            (hunk_ref.hunk for hunk_ref in hunks)
        )
    )


def real_saved_start(hunk):
    # For removal only hunks git reports a line decremented by one. We reverse
    # compensate here
    return hunk.saved_start + (1 if hunk_of_removals_only(hunk) else 0)


def hunk_of_removals_only(hunk):
    # Note that this can only ever be true for zero context diffs
    return hunk.saved_length == 0 and hunk.head_length > 0


def compute_identifier_for_view(view):
    # type: (sublime.View) -> Optional[Tuple]
    settings = view.settings()
    return (
        settings.get("git_savvy.repo_path"),
        settings.get("git_savvy.file_path"),
        settings.get("git_savvy.inline_diff_view.base_commit"),
        settings.get("git_savvy.inline_diff_view.target_commit"),
    ) if settings.get("git_savvy.inline_diff_view") else None


def is_inline_diff_view(view):
    # type: (sublime.View) -> bool
    return view.settings().get('git_savvy.inline_diff_view')


def is_interactive_diff(view):
    # type: (sublime.View) -> bool
    settings = view.settings()
    return (
        not settings.get("git_savvy.inline_diff_view.base_commit")
        and not settings.get("git_savvy.inline_diff_view.target_commit")
    )


class gs_inline_diff(WindowCommand, GitCommand):

    """
    Given an open file in a git-tracked directory, show a new view with the
    diff (against HEAD) displayed inline.  Allow the user to stage or reset
    hunks or individual lines, and to navigate between hunks.
    """

    def run(self, cached=None, match_current_position="<SENTINEL>"):
        # type: (Optional[bool], object) -> None
        if match_current_position != "<SENTINEL>":
            print(
                'GitSavvy: Argument "match_current_position" to "gs_inline_diff" is deprecated '
                'and not evaluated.  You should remove it from the binding.'
            )

        active_view = self.window.active_view()
        assert active_view
        # Let this command act like a toggle
        if is_inline_diff_view(active_view) and (
            cached is None
            or active_view.settings().get('git_savvy.inline_diff_view.in_cached_mode') == cached
        ):
            active_view.close()
            return

        if active_view.settings().get("git_savvy.diff_view"):
            self.open_from_diff_view(active_view)
        elif (
            # Or: cursor matches the scope `git-savvy.commit git-savvy.diff`
            active_view.settings().get("git_savvy.line_history_view")
            or active_view.settings().get("git_savvy.show_commit_view")
        ):
            self.open_from_commit_info(active_view)
        elif active_view.settings().get("git_savvy.show_file_at_commit_view"):
            self.open_from_show_file_at_commit_view(active_view)
        else:
            file_path = self.file_path
            if not file_path:
                flash(active_view, "Cannot show diff for unnamed buffers.")
                return

            is_ordinary_view = bool(active_view.file_name())
            if is_ordinary_view:
                syntax_file = active_view.settings().get("syntax")
                cur_pos = capture_cur_position(active_view)
                if cur_pos and cached:
                    row, col, offset = cur_pos
                    new_row = self.find_matching_lineno(None, None, row + 1, file_path) - 1
                    cur_pos = Position(new_row, col, offset)
            else:
                syntax_file = util.file.guess_syntax_for_file(self.window, file_path)
                cur_pos = None

            self.window.run_command("gs_inline_diff_open", {
                "repo_path": self.repo_path,
                "file_path": file_path,
                "syntax": syntax_file,
                "cached": bool(cached),
                "match_position": cur_pos
            })

    def open_from_diff_view(self, view):
        # type: (sublime.View) -> None
        settings = view.settings()
        repo_path = settings.get("git_savvy.repo_path")
        cached = settings.get("git_savvy.diff_view.in_cached_mode")
        cursor = view.sel()[0].b

        jump_position = diff.jump_position_to_file(
            view,
            SplittedDiff.from_view(view),
            cursor
        )
        if not jump_position:
            # Try to recover, maybe the diff is clean etc.
            file_path = settings.get("git_savvy.file_path")
            if not file_path:
                flash(view, "Cannot show diff for unnamed buffers.")
                return

            self.window.run_command("gs_inline_diff_open", {
                "repo_path": repo_path,
                "file_path": file_path,
                "syntax": util.file.guess_syntax_for_file(self.window, file_path),
                "cached": bool(cached),
                "match_position": None
            })
            return

        if jump_position.commit_hash:
            raise RuntimeError(
                "Assertion failed! "
                "Historical diffs shouldn't have `jump_position.commit_hash`.")

        file_path = os.path.normpath(os.path.join(repo_path, jump_position.filename))
        syntax_file = util.file.guess_syntax_for_file(self.window, file_path)
        base_commit = settings.get("git_savvy.diff_view.base_commit")
        target_commit = settings.get("git_savvy.diff_view.target_commit")

        cur_pos = Position(
            jump_position.line - 1,
            jump_position.col - 1,
            y_offset(view, cursor)
        )
        if cached:
            row, col, offset = cur_pos
            new_row = self.find_matching_lineno(None, None, row + 1, file_path) - 1
            cur_pos = Position(new_row, col, offset)

        self.window.run_command("gs_inline_diff_open", {
            "repo_path": repo_path,
            "file_path": file_path,
            "syntax": syntax_file,
            "cached": bool(cached),
            "match_position": cur_pos,
            "base_commit": base_commit,
            "target_commit": target_commit
        })

    def open_from_show_file_at_commit_view(self, view):
        # type: (sublime.View) -> None
        settings = view.settings()
        repo_path = settings.get("git_savvy.repo_path")
        file_path = settings.get("git_savvy.file_path")
        target_commit = settings.get("git_savvy.show_file_at_commit_view.commit")
        base_commit = self.previous_commit(target_commit, file_path)
        self.window.run_command("gs_inline_diff_open", {
            "repo_path": repo_path,
            "file_path": file_path,
            "syntax": settings.get("syntax"),
            "cached": False,
            "match_position": capture_cur_position(view),
            "base_commit": base_commit,
            "target_commit": target_commit
        })

    def open_from_commit_info(self, view):
        # type: (sublime.View) -> None
        settings = view.settings()
        repo_path = settings.get("git_savvy.repo_path")
        cursor = view.sel()[0].b
        jump_position = diff.jump_position_to_file(
            view,
            SplittedDiff.from_view(view),
            cursor
        )
        if not jump_position:
            flash(view, "Could not parse for a filename and position at cursor position.")
            return

        if not jump_position.commit_hash:
            flash(view, "Could not parse for a commit hash at cursor position.")
            return

        file_path = os.path.normpath(os.path.join(repo_path, jump_position.filename))
        syntax_file = util.file.guess_syntax_for_file(self.window, file_path)
        target_commit = jump_position.commit_hash
        base_commit = self.previous_commit(target_commit, file_path)
        cur_pos = Position(
            jump_position.line - 1,
            jump_position.col - 1,
            y_offset(view, cursor)
        )
        self.window.run_command("gs_inline_diff_open", {
            "repo_path": repo_path,
            "file_path": file_path,
            "syntax": syntax_file,
            "cached": False,
            "match_position": cur_pos,
            "base_commit": base_commit,
            "target_commit": target_commit
        })


@contextmanager
def disabled_on_activated():
    global active_on_activated
    active_on_activated = False
    try:
        yield
    finally:
        active_on_activated = True


class gs_inline_diff_open(WindowCommand, GitCommand):
    def run(
        self,
        repo_path,
        file_path,
        syntax,
        cached=False,
        match_position=None,
        base_commit=None,
        target_commit=None
    ):
        # type: (str, str, str, bool, Position, str, str) -> None
        active_view = self.window.active_view()
        this_id = (repo_path, file_path, base_commit, target_commit)
        for view in self.window.views():
            if compute_identifier_for_view(view) == this_id:
                diff_view = view
                settings = diff_view.settings()
                settings.set("git_savvy.inline_diff_view.in_cached_mode", cached)
                with disabled_on_activated():
                    focus_view(diff_view)
                if active_view:
                    place_view(self.window, diff_view, after=active_view)
                break

        else:
            _title = INLINE_DIFF_CACHED_TITLE if cached else INLINE_DIFF_TITLE
            title = _title.format(os.path.basename(file_path))
            diff_view = util.view.create_scratch_view(self.window, "inline_diff", {
                "title": title,
                "syntax": syntax,
                "git_savvy.repo_path": repo_path,
                "git_savvy.file_path": file_path,
                "git_savvy.inline_diff_view.in_cached_mode": cached,
                "git_savvy.inline_diff_view.base_commit": base_commit,
                "git_savvy.inline_diff_view.target_commit": target_commit,
            })
            show_file_at_commit.pass_next_commits_info_along(active_view, to=diff_view)
            diff_view.run_command("gs_handle_vintageous")

        diff_view.run_command("gs_inline_diff_refresh", {
            "match_position": match_position,
            "sync": True
        })


class gs_inline_diff_refresh(TextCommand, GitCommand):

    """
    Diff one version of a file (the base) against another, and display the
    changes inline.

    If not in `cached` mode, compare the file in the working tree against the
    same file in the index.  If a line or hunk is selected and the primary
    action for the view is taken (pressing `l` or `h` for line or hunk,
    respectively), add that line/hunk to the index.  If a line or hunk is
    selected and the secondary action for the view is taken (pressing `L` or
    `H`), remove those changes from the file in the working tree.

    If in `cached` mode, compare the file in the index against the same file
    in the HEAD.  If a link or hunk is selected and the primary action for
    the view is taken, remove that line from the index.  Secondary actions
    are not supported in `cached` mode.
    """

    def run(self, edit, sync=True, match_position=None, raw_diff=None):
        # type: (sublime.Edit, bool, Optional[Position], Optional[str]) -> None
        if sync:
            self._run(sync, match_position, raw_diff)
        else:
            sublime.set_timeout_async(lambda: self._run(sync, match_position, raw_diff))

    def _run(self, runs_on_ui_thread, match_position, raw_diff):
        # type: (bool, Optional[Position], Optional[str]) -> None
        settings = self.view.settings()
        file_path = settings.get("git_savvy.file_path")
        in_cached_mode = settings.get("git_savvy.inline_diff_view.in_cached_mode")
        base_commit = settings.get("git_savvy.inline_diff_view.base_commit")
        target_commit = settings.get("git_savvy.inline_diff_view.target_commit")
        ignore_eol_ws = self.savvy_settings.get("inline_diff_ignore_eol_whitespaces", True)
        if target_commit and not base_commit:
            target_commit = "{}^".format(target_commit)

        if raw_diff is None:
            raw_diff_output = self.git(
                "diff",
                "--no-color",
                "-U0",
                "--ignore-space-at-eol" if ignore_eol_ws else None,
                "--cached" if in_cached_mode else None,
                base_commit,
                target_commit,
                "--",
                file_path,
                decode=False
            )
            encodings = self.get_encoding_candidates()
            try:
                raw_diff, encoding = self.try_decode(raw_diff_output, encodings)
            except UnicodeDecodeError:
                sublime.error_message(DECODE_ERROR_MESSAGE)
                self.view.close()
                return
            settings.set("git_savvy.inline_diff.encoding", encoding)

        try:
            diff = util.parse_diff(raw_diff)
        except UnsupportedCombinedDiff:
            sublime.error_message("Inline-diff cannot be displayed for this file - "
                                  "it has a merge conflict.")
            self.view.close()
            return

        if not diff and self.is_probably_untracked_file(file_path):
            flash(self.view, "Inline-diff cannot be displayed for untracked files.")
            self.view.close()
            return

        if is_interactive_diff(self.view):
            hunks_count = len(diff)
            flash(self.view, "File has {} {} {}".format(
                hunks_count,
                "staged" if in_cached_mode else "unstaged",
                "hunk" if hunks_count == 1 else "hunks"
            ))

        if in_cached_mode:
            original_content = self.get_file_content_at_commit(file_path, "HEAD")
        elif target_commit and not base_commit:
            # For historical diffs, not having a `base_commit` means we
            # have reached the initial revision of a file. The base content
            # we're coming from is thus the empty "".
            original_content = ""
        else:
            original_content = self.get_file_content_at_commit(file_path, base_commit)
        inline_diff_contents, hunks = self.get_inline_diff_contents(original_content, diff)

        _title = INLINE_DIFF_CACHED_TITLE if in_cached_mode else INLINE_DIFF_TITLE
        title = _title.format(os.path.basename(file_path))
        if target_commit:
            title += (
                ", ({}..{})".format(
                    self.get_short_hash(base_commit),
                    self.get_short_hash(target_commit),
                )
                if base_commit
                else ", (initial version)"
            )
        elif base_commit:
            title += ", ({}..WORKING DIR)".format(self.get_short_hash(base_commit))
        if runs_on_ui_thread:
            self.draw(self.view, title, match_position, inline_diff_contents, hunks)
        else:
            enqueue_on_ui(self.draw, self.view, title, match_position, inline_diff_contents, hunks)

    def draw(self, view, title, match_position, inline_diff_contents, hunks):
        navigate_to_first_hunk = (
            match_position is None
            and view.size() == 0  # t.i. only on the initial draw!
            and self.savvy_settings.get("inline_diff_auto_scroll", True)
        )

        with reapply_possible_fold(view):
            replace_view_content(view, inline_diff_contents)
            view.set_name(title)

            if match_position:
                row, col, row_offset = match_position
                new_row = translate_row_to_inline_diff(view, row)
                apply_position(view, new_row, col, row_offset)
            elif navigate_to_first_hunk:
                view.run_command("gs_inline_diff_navigate_hunk")

            self.highlight_regions(hunks)

    def get_inline_diff_contents(self, original_contents, diff):
        # type: (str, List[InlineDiff_Hunk]) -> Tuple[str, List[HunkReference]]
        """
        Given a file's original contents and an array of hunks that could be
        applied to it, return a string with the diff lines inserted inline.
        Also return an array of inlined-hunk information to be used for
        diff highlighting.

        Remove any `-` or `+` characters at the beginning of each line, as
        well as the header summary line.  Additionally, store relevant data
        in `diff_view_hunks` to be used when the user takes an
        action in the view.
        """
        lines = original_contents.splitlines(keepends=True)
        hunks = []  # type: List[HunkReference]
        adjustment = 0

        for hunk in diff:
            # Git line-numbers are 1-indexed, lists are 0-indexed.
            head_start = hunk.head_start - 1
            # If the change includes only added lines, the head_start value
            # will be off-by-one.
            head_start += 1 if hunk.head_length == 0 else 0
            head_end = head_start + hunk.head_length

            # Remove the `@@` header line.
            diff_lines = hunk.raw_lines[1:]

            section_start = head_start + adjustment
            section_end = section_start + len(diff_lines)
            line_types = [line[0] for line in diff_lines]
            raw_lines = [line[1:] for line in diff_lines]

            # Store information about this hunk, with proper references, so actions
            # can be taken when triggered by the user (e.g. stage line X in diff_view).
            hunks.append(HunkReference(
                section_start, section_end, hunk, line_types, raw_lines
            ))

            tail = lines[head_end + adjustment + (1 if line_types[-1] == "\\" else 0):]
            lines = lines[:section_start] + raw_lines + tail
            adjustment += len(diff_lines) - hunk.head_length

        diff_view_hunks[self.view.id()] = hunks
        return "".join(lines), hunks

    def highlight_regions(self, replaced_lines):
        # type: (List[HunkReference]) -> None
        """
        Given an array of tuples, where each tuple contains the start and end
        of an inlined diff hunk as well as an array of line-types (add/remove)
        for the lines in that hunk, highlight the added regions in green and
        the removed regions in red.
        """
        add_regions = []  # type: List[sublime.Region]
        add_bold_regions = []
        remove_regions = []  # type: List[sublime.Region]
        remove_bold_regions = []

        for section_start, section_end, hunk, line_types, raw_lines in replaced_lines:
            for line_type, lines_ in groupby(
                range(section_start, section_end),
                key=lambda line: line_types[line - section_start]
            ):
                lines = list(lines_)
                start, end = lines[0], lines[-1]
                start_line = self.view.full_line(self.view.text_point(start, 0))
                end_line = (
                    self.view.full_line(self.view.text_point(end, 0))
                    if start != end
                    else start_line
                )
                region = sublime.Region(start_line.begin(), end_line.end())
                container = add_regions if line_type == "+" else remove_regions
                container.append(region)

            # For symmetric modifications show highlighting for the in-line changes
            if sum(1 if t == "+" else -1 for t in line_types) == 0:
                # Determine start of hunk/section.
                section_start_idx = self.view.text_point(section_start, 0)

                # Removed lines come first in a hunk.
                remove_start = section_start_idx
                first_added_line = line_types.index("+")
                add_start = section_start_idx + len("".join(raw_lines[:first_added_line]))

                removed_part = "".join(raw_lines[:first_added_line])
                added_part = "".join(raw_lines[first_added_line:])
                changes = util.diff_string.get_changes(removed_part, added_part)

                for change in changes:
                    if change.type in (util.diff_string.DELETE, util.diff_string.REPLACE):
                        # Display bold color in removed hunk area.
                        region_start = remove_start + change.old_start
                        region_end = remove_start + change.old_end
                        remove_bold_regions.append(sublime.Region(region_start, region_end))

                    if change.type in (util.diff_string.INSERT, util.diff_string.REPLACE):
                        # Display bold color in added hunk area.
                        region_start = add_start + change.new_start
                        region_end = add_start + change.new_end
                        add_bold_regions.append(sublime.Region(region_start, region_end))

        self.view.add_regions(
            "git-savvy-added-lines",
            add_regions,
            scope="diff.inserted.git-savvy.inline-diff",
            flags=sublime.RegionFlags.NO_UNDO
        )
        self.view.add_regions(
            "git-savvy-removed-lines",
            remove_regions,
            scope="diff.deleted.git-savvy.inline-diff",
            flags=sublime.RegionFlags.NO_UNDO
        )
        self.view.add_regions(
            "git-savvy-added-bold",
            add_bold_regions,
            scope="diff.inserted.char.git-savvy.inline-diff",
            flags=sublime.RegionFlags.NO_UNDO
        )
        self.view.add_regions(
            "git-savvy-removed-bold",
            remove_bold_regions,
            scope="diff.deleted.char.git-savvy.inline-diff",
            flags=sublime.RegionFlags.NO_UNDO
        )


@contextmanager
def reapply_possible_fold(view):
    current_fold_mode = fold_mode(view)
    if current_fold_mode == "ab":
        yield
    else:
        view.run_command("unfold_all")
        yield
        view.run_command("gs_inline_diff_toggle_side", {"side": current_fold_mode})


def fold_mode(view):
    # type: (sublime.View) -> Literal["a", "b", "ab"]
    currently_folded = view.folded_regions()
    if not currently_folded:
        return "ab"
    if currently_folded == regions(view, "b"):
        return "a"
    if currently_folded == regions(view, "a"):
        return "b"
    return "ab"


def regions(view, side):
    # type: (sublime.View, Literal["a", "b"]) -> List[sublime.Region]
    selector = "git-savvy-removed-lines" if side == "a" else "git-savvy-added-lines"
    return [sublime.Region(r.a, r.b - 1) for r in view.get_regions(selector)]


class gs_inline_diff_toggle_side(TextCommand, GitCommand):
    def run(self, edit, side):
        # type: (sublime.Edit, Literal["a", "b"]) -> None
        view = self.view
        currently_folded = view.folded_regions()

        if side == "a":
            b_regions = regions(view, "b")
            if currently_folded:
                view.run_command("unfold_all")
            if currently_folded == b_regions:
                return
            view.fold(b_regions)
        else:
            a_regions = regions(view, "a")
            if currently_folded:
                view.run_command("unfold_all")
            if currently_folded == a_regions:
                return
            view.fold(a_regions)


class gs_inline_diff_toggle_cached_mode(TextCommand, GitCommand):

    """
    Toggle `in_cached_mode`.
    """

    def is_enabled(self, *args, **kwargs):
        return is_interactive_diff(self.view)

    def run(self, edit):
        settings = self.view.settings()
        in_cached_mode = settings.get("git_savvy.inline_diff_view.in_cached_mode")
        next_mode = not in_cached_mode
        settings.set("git_savvy.inline_diff_view.in_cached_mode", next_mode)

        next_diff = None
        cur_pos = capture_cur_position(self.view)
        if cur_pos:
            row, col, offset = cur_pos
            line_no, col_no = translate_pos_from_diff_view_to_file(self.view, row + 1, col + 1)
            file_path = self.file_path
            if in_cached_mode:
                next_diff = self.git("diff", "-U0", "--", file_path)
                new_row = self.adjust_line_according_to_diff(next_diff, line_no) - 1
            else:
                hunks = [hunk_ref.hunk for hunk_ref in diff_view_hunks[self.view.id()]]
                new_row = self.reverse_adjust_line_according_to_hunks(hunks, line_no) - 1
            cur_pos = Position(new_row, col, offset)

        self.view.run_command("gs_inline_diff_refresh", {
            "match_position": cur_pos,
            "sync": True,
            "raw_diff": next_diff,
        })


class GsInlineDiffFocusEventListener(EventListener):

    """
    If the current view is an inline-diff view, refresh the view with
    latest file status when the view regains focus.
    """

    def on_activated(self, view: sublime.View) -> None:
        if (
            active_on_activated
            and is_inline_diff_view(view)
            and (
                not view.settings().get("git_savvy.inline_diff_view.target_commit")
                or view.id() not in diff_view_hunks
            )
        ):
            view.run_command("gs_inline_diff_refresh", {"sync": False})

    def on_post_save(self, view: sublime.View) -> None:
        for other_view in other_visible_views(view):
            if (
                is_inline_diff_view(other_view)
                and other_view.settings().get("git_savvy.file_path") == view.file_name()
                and not other_view.settings().get("git_savvy.inline_diff_view.target_commit")
                and not other_view.settings().get("git_savvy.inline_diff_view.in_cached_mode")
            ):
                other_view.run_command("gs_inline_diff_refresh", {"sync": False})


class gs_inline_diff_stage_or_reset_base(TextCommand, GitCommand):

    """
    Base class for any stage or reset operation in the inline-diff view.
    Determine the line number of the current cursor location, and use that
    to determine what diff to apply to the file (implemented in subclass).
    """

    def is_enabled(self, *args, **kwargs):
        return is_interactive_diff(self.view)

    def run(self, edit, **kwargs):
        enqueue_on_worker(self.run_async, **kwargs)

    def run_async(self, reset=False):
        # type: (bool) -> None
        in_cached_mode = self.view.settings().get("git_savvy.inline_diff_view.in_cached_mode")
        if in_cached_mode and reset:
            flash(self.view, "Can't discard staged changes.  Unstage first.")
            return None

        ignore_ws = (
            "--ignore-whitespace"
            if self.savvy_settings.get("inline_diff_ignore_eol_whitespaces", True)
            else None
        )
        frozen_sel = [s for s in self.view.sel()]
        if len(frozen_sel) != 1 or not frozen_sel[0].empty():
            flash(self.view, "Only single cursors are supported.")
            return

        row, _ = self.view.rowcol(frozen_sel[0].begin())
        diff_lines = self.get_diff_from_line(row, reset)
        if not diff_lines:
            flash(self.view, "Not on a hunk.")
            return

        header = DIFF_HEADER.format(path=self.get_rel_path())
        full_diff = header + diff_lines + "\n"

        # The three argument combinations below result from the following
        # three scenarios:
        #
        # 1) The user is in non-cached mode and wants to stage a line/hunk, so
        #    do NOT apply the patch in reverse, but do apply it only against
        #    the cached/indexed file (not the working tree).
        # 2) The user is in non-cached mode and wants to undo a line/hunk, so
        #    DO apply the patch in reverse, and do apply it both against the
        #    index and the working tree.
        # 3) The user is in cached mode and wants to undo a line/hunk, so DO
        #    apply the patch in reverse, but only apply it against the cached/
        #    indexed file.
        #
        # Note: When in cached mode, the action taken will always be to apply
        #       the patch in reverse only to the index.

        args = [
            "apply",
            "--unidiff-zero",
            "--reverse" if (reset or in_cached_mode) else None,
            "--cached" if (not reset or in_cached_mode) else None,
            ignore_ws,
            "-"
        ]
        encoding = self.view.settings().get('git_savvy.inline_diff.encoding', 'utf-8')

        self.git(*args, stdin=full_diff, stdin_encoding=encoding)
        self.save_to_history(args, full_diff, encoding)

        if reset or in_cached_mode or self.name() == "gs_inline_diff_stage_or_reset_line":
            cur_pos = None
        else:
            cur_pos = capture_cur_position(self.view)
            if cur_pos:
                row, col, offset = cur_pos
                line_no, col_no = translate_pos_from_diff_view_to_file(self.view, row + 1, col + 1)
                cur_pos = Position(line_no - 1, col_no - 1, offset)

        self.view.run_command("gs_inline_diff_refresh", {
            "match_position": cur_pos,
            "sync": True
        })

    def save_to_history(self, args, full_diff, encoding):
        """
        After successful `git apply`, save the apply-data into history
        attached to the view, for later Undo.
        """
        history = self.view.settings().get("git_savvy.inline_diff.history") or []
        history.append((args, full_diff, encoding))
        self.view.settings().set("git_savvy.inline_diff.history", history)

    def get_diff_from_line(self, row, reset):
        # type: (Row, bool) -> Optional[str]
        raise NotImplementedError


class gs_inline_diff_stage_or_reset_line(gs_inline_diff_stage_or_reset_base):

    """
    Given a line number, generate a diff of that single line in the active
    file, and apply that diff to the file.  If the `reset` flag is set to
    `True`, apply the patch in reverse (reverting that line to the version
    in HEAD).
    """

    def get_diff_from_line(self, row, reset):
        # type: (Row, bool) -> Optional[str]
        hunks = diff_view_hunks[self.view.id()]
        add_length_earlier_in_diff = 0
        cur_hunk_begin_on_minus = 0
        cur_hunk_begin_on_plus = 0

        # Find the correct hunk.
        for hunk_ref in hunks:
            if hunk_ref.section_start <= row < hunk_ref.section_end:
                break
            else:
                # we loop through all hooks before selected hunk.
                # used create a correct diff when stage, unstage
                # need to make undo work properly.
                for type in hunk_ref.line_types:
                    if type == "+":
                        add_length_earlier_in_diff += 1
                    elif type == "-":
                        add_length_earlier_in_diff -= 1
        else:
            return None

        # Determine head/staged starting line.
        index_in_hunk = row - hunk_ref.section_start
        assert index_in_hunk >= 0
        line = hunk_ref.lines[index_in_hunk]
        line_type = hunk_ref.line_types[index_in_hunk]

        # need to make undo work properly when undoing
        # a specific line.
        for type in hunk_ref.line_types[:index_in_hunk]:
            if type == "-":
                cur_hunk_begin_on_minus += 1
            elif type == "+":
                cur_hunk_begin_on_plus += 1

        # Removed lines are always first with `git diff -U0 ...`. Therefore, the
        # line to remove will be the Nth line, where N is the line index in the hunk.
        head_start = (
            hunk_ref.hunk.head_start
            if line_type == "+"
            else hunk_ref.hunk.head_start + index_in_hunk
        )

        if reset:
            xhead_start = head_start - index_in_hunk
            if line_type != "+":
                xhead_start += add_length_earlier_in_diff

            return (
                "@@ -{head_start},{head_length} +{new_start},{new_length} @@\n"
                "{line_type}{line}"
                .format(
                    head_start=(xhead_start if xhead_start >= 0 else cur_hunk_begin_on_plus),
                    head_length="0" if line_type == "+" else "1",
                    # If head_length is zero, diff will report original start position
                    # as one less than where the content is inserted, for example:
                    #   @@ -75,0 +76,3 @@
                    new_start=xhead_start + (1 if line_type == "+" else 0),

                    new_length="1" if line_type == "+" else "0",
                    line_type=line_type,
                    line=line
                )
            )

        else:
            head_start += 1
            return (
                "@@ -{head_start},{head_length} +{new_start},{new_length} @@\n"
                "{line_type}{line}"
                .format(
                    head_start=head_start + (-1 if line_type == "-" else 0),
                    head_length="0" if line_type == "+" else "1",
                    # If head_length is zero, diff will report original start position
                    # as one less than where the content is inserted, for example:
                    #   @@ -75,0 +76,3 @@
                    new_start=head_start + (-1 if line_type == "-" else 0),
                    new_length="1" if line_type == "+" else "0",
                    line_type=line_type,
                    line=line
                )
            )


class gs_inline_diff_stage_or_reset_hunk(gs_inline_diff_stage_or_reset_base):

    """
    Given a line number, generate a diff of the hunk containing that line,
    and apply that diff to the file.  If the `reset` flag is set to `True`,
    apply the patch in reverse (reverting that hunk to the version in HEAD).
    """

    def get_diff_from_line(self, row, reset):
        # type: (Row, bool) -> Optional[str]
        hunks = diff_view_hunks[self.view.id()]
        add_length_earlier_in_diff = 0

        # Find the correct hunk.
        for hunk_ref in hunks:
            if hunk_ref.section_start <= row < hunk_ref.section_end:
                break
            else:
                # we loop through all hooks before selected hunk.
                # used create a correct diff when stage, unstage
                # need to make undo work properly.
                for type in hunk_ref.line_types:
                    if type == "+":
                        add_length_earlier_in_diff += 1
                    elif type == "-":
                        add_length_earlier_in_diff -= 1
        else:
            return None

        stand_alone_header = (
            "@@ -{head_start},{head_length} +{new_start},{new_length} @@\n".format(
                head_start=hunk_ref.hunk.head_start + (add_length_earlier_in_diff if reset else 0),
                head_length=hunk_ref.hunk.head_length,
                # If head_length is zero, diff will report original start position
                # as one less than where the content is inserted, for example:
                #   @@ -75,0 +76,3 @@
                new_start=hunk_ref.hunk.head_start + (0 if hunk_ref.hunk.head_length else 1),
                new_length=hunk_ref.hunk.saved_length
            )
        )

        return "".join([stand_alone_header] + hunk_ref.hunk.raw_lines[1:])


class gs_inline_diff_previous_commit(TextCommand, GitCommand):
    def run(self, edit):
        # type: (...) -> None
        view = self.view
        settings = view.settings()
        file_path = settings.get("git_savvy.file_path")
        if is_interactive_diff(view):
            base_commit = self.recent_commit("HEAD", file_path)
            if not base_commit:
                flash(view, "No historical version of that file found.")
                return

        else:
            base_commit = settings.get("git_savvy.inline_diff_view.base_commit")
            if not base_commit:
                flash(view, "Already on the initial revision.")
                return

        new_target_commit = base_commit
        new_base_commit = self.previous_commit(base_commit, file_path)
        if new_base_commit:
            show_file_at_commit.remember_next_commit_for(view, {new_base_commit: base_commit})
        settings.set("git_savvy.inline_diff_view.base_commit", new_base_commit)
        settings.set("git_savvy.inline_diff_view.target_commit", new_target_commit)

        pos = capture_cur_position(view)
        if pos:
            row, col, offset = pos
            line_no, col_no = translate_pos_from_diff_view_to_file(view, row + 1, col + 1)
            hunks = [hunk_ref.hunk for hunk_ref in diff_view_hunks[self.view.id()]]
            line_no = self.reverse_adjust_line_according_to_hunks(hunks, line_no)
            pos = Position(line_no - 1, col_no - 1, offset)

        self.view.run_command("gs_inline_diff_refresh", {
            "match_position": pos,
            "sync": True
        })
        flash(view, "On commit {}".format(new_target_commit))


class gs_inline_diff_next_commit(TextCommand, GitCommand):
    def run(self, edit):
        view = self.view
        settings = view.settings()
        file_path = settings.get("git_savvy.file_path")
        if is_interactive_diff(view):
            flash(view, "Already on the working dir version.")
            return

        target_commit = settings.get("git_savvy.inline_diff_view.target_commit")
        new_base_commit = target_commit
        try:
            new_target_commit = show_file_at_commit.get_next_commit(self, view, target_commit, file_path)
        except ValueError:
            flash(view, "Can't find a newer commit; it looks orphaned.")
            return

        if not new_target_commit:
            new_base_commit = None

        settings.set("git_savvy.inline_diff_view.base_commit", new_base_commit)
        settings.set("git_savvy.inline_diff_view.target_commit", new_target_commit)

        pos = capture_cur_position(view)
        diff = None
        if pos:
            row, col, offset = pos
            line_no, col_no = translate_pos_from_diff_view_to_file(view, row + 1, col + 1)
            diff = self.no_context_diff(target_commit, new_target_commit, file_path)
            line_no = self.adjust_line_according_to_diff(diff, line_no)
            pos = Position(line_no - 1, col_no - 1, offset)

        self.view.run_command("gs_inline_diff_refresh", {
            "match_position": pos,
            "sync": True,
            "raw_diff": diff
        })
        if new_target_commit:
            flash(view, "On commit {}".format(new_target_commit))
        else:
            flash(view, "On working dir version")


class gs_inline_diff_open_file(TextCommand, GitCommand):

    """
    Opens an editable view of the file being diff'd.
    """

    @util.view.single_cursor_coords
    def run(self, coords, edit):
        window = self.view.window()
        if not window:
            return

        if not coords:
            return
        row, col = coords

        settings = self.view.settings()
        file_path = settings.get("git_savvy.file_path")
        line_no, col_no = translate_pos_from_diff_view_to_file(self.view, row + 1, col + 1)
        if is_interactive_diff(self.view):
            if settings.get("git_savvy.inline_diff_view.in_cached_mode"):
                diff = self.git("diff", "-U0", "--", file_path)
                line_no = self.adjust_line_according_to_diff(diff, line_no)
        else:
            target_commit = settings.get("git_savvy.inline_diff_view.target_commit")
            line_no = self.find_matching_lineno(target_commit, None, line_no, file_path)
        self.open_file(window, file_path, line_no, col_no)

    def open_file(self, window, file_path, line_no, col_no):
        # type: (sublime.Window, str, LineNo, ColNo) -> None
        window.open_file(
            "{file}:{line_no}:{col_no}".format(
                file=file_path,
                line_no=line_no,
                col_no=col_no
            ),
            sublime.ENCODED_POSITION
        )


class gs_inline_diff_open_file_at_hunk(TextCommand, GitCommand):
    def run(self, edit):
        view = self.view
        if is_interactive_diff(view):
            view.run_command("gs_inline_diff_open_file")
            return

        window = view.window()
        if not window:
            return
        settings = view.settings()
        file_path = settings.get("git_savvy.file_path")
        target_commit = settings.get("git_savvy.inline_diff_view.target_commit")

        pos = capture_cur_position(view)
        if pos:
            row, col, offset = pos
            line_no, col_no = translate_pos_from_diff_view_to_file(view, row + 1, col + 1)
            pos = Position(line_no - 1, col_no - 1, offset)

        window.run_command("gs_show_file_at_commit", {
            "commit_hash": target_commit,
            "filepath": file_path,
            "position": pos,
            "lang": view.settings().get('syntax')
        })


class gs_inline_diff_open_graph_context(TextCommand, GitCommand):
    def run(self, edit):
        view = self.view
        window = view.window()
        if not window:
            return

        settings = view.settings()
        target_commit = settings.get("git_savvy.inline_diff_view.target_commit")
        window.run_command("gs_graph", {
            "all": True,
            "follow": self.get_short_hash(target_commit) if target_commit else "HEAD",
        })


def translate_pos_from_diff_view_to_file(view, line_no, col_no=1):
    # type: (sublime.View, LineNo, ColNo) -> Tuple[LineNo, ColNo]
    hunks = diff_view_hunks[view.id()]
    hunk_ref = closest_hunk_ref_before_line(hunks, line_no)

    # No diff hunks exist before the selected line.
    if not hunk_ref:
        return line_no, col_no

    # The selected line is within the hunk.
    if hunk_ref.section_end >= line_no:
        hunk_change_index = line_no - hunk_ref.section_start - 1
        change = hunk_ref.hunk.changes[hunk_change_index]
        # For removed lines, we use the previous or next visible line.
        # We reset the column "1".
        return change.saved_pos, col_no if change.type == "+" else 1

    # The selected line is after the hunk.
    else:
        lines_after_hunk_end = line_no - hunk_ref.section_end - 1
        hunk_end_in_saved = real_saved_start(hunk_ref.hunk) + hunk_ref.hunk.saved_length
        return hunk_end_in_saved + lines_after_hunk_end, col_no


def closest_hunk_ref_before_line(hunks, line):
    # type: (List[HunkReference], LineNo) -> Optional[HunkReference]
    for hunk_ref in reversed(hunks):
        if hunk_ref.section_start < line:
            return hunk_ref
    else:
        return None


class gs_inline_diff_navigate_hunk(GsNavigate):

    """
    Navigate to the next/previous hunk that appears after the current cursor
    position.
    """
    offset = 0
    log_position = True
    first_region_may_expand_to_bof = False

    def get_available_regions(self):
        return [
            sublime.Region(
                self.view.text_point(hunk.section_start, 0),
                self.view.text_point(hunk.section_end + 1, 0))
            for hunk in diff_view_hunks[self.view.id()]]


class gs_inline_diff_undo(TextCommand, GitCommand):

    """
    Undo the last action taken in the inline-diff view, if possible.
    """

    def is_enabled(self, *args, **kwargs):
        return is_interactive_diff(self.view)

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        history = self.view.settings().get("git_savvy.inline_diff.history") or []
        if not history:
            flash(self.view, "Undo stack is empty")
            return

        last_args, last_stdin, encoding = history.pop()
        # Toggle the `--reverse` flag.
        was_reset = last_args[2] and not last_args[3]
        last_args[2] = "--reverse" if not last_args[2] else None

        self.git(*last_args, stdin=last_stdin, stdin_encoding=encoding)
        self.view.settings().set("git_savvy.inline_diff.history", history)

        cur_pos = capture_cur_position(self.view) if not was_reset else None
        if cur_pos is not None:
            row, col, offset = cur_pos
            line_no, col_no = translate_pos_from_diff_view_to_file(self.view, row + 1, col + 1)
            cur_pos = Position(line_no - 1, col_no - 1, offset)

        self.view.run_command("gs_inline_diff_refresh", {
            "match_position": cur_pos,
            "sync": True
        })
