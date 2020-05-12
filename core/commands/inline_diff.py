import os
from collections import namedtuple

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from .navigate import GsNavigate
from ..git_command import GitCommand
from ..utils import flash, focus_view
from ..runtime import enqueue_on_ui
from ..view import replace_view_content
from ...common import util


__all__ = (
    "gs_inline_diff",
    "gs_inline_diff_refresh",
    "gs_inline_diff_stage_or_reset_line",
    "gs_inline_diff_stage_or_reset_hunk",
    "gs_inline_diff_open_file",
    "gs_inline_diff_navigate_hunk",
    "gs_inline_diff_undo",
    "GsInlineDiffFocusEventListener",
)


MYPY = False
if MYPY:
    from typing import Optional, Tuple


HunkReference = namedtuple("HunkReference", ("section_start", "section_end", "hunk", "line_types", "lines"))


INLINE_DIFF_TITLE = "DIFF: "
INLINE_DIFF_CACHED_TITLE = "DIFF (cached): "

DIFF_HEADER = """diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
"""

diff_view_hunks = {}


def capture_cur_position(view):
    # type: (sublime.View) -> Optional[Tuple[int, int, float]]
    try:
        sel = view.sel()[0]
    except Exception:
        return None

    row, col = view.rowcol(sel.begin())
    vx, vy = view.viewport_position()
    row_offset = row - (vy / view.line_height())
    return row, col, row_offset


def place_cursor_and_show(view, row, col, row_offset):
    # type: (sublime.View, int, int, float) -> None
    view.sel().clear()
    pt = view.text_point(row, col)
    view.sel().add(sublime.Region(pt, pt))

    vy = (row - row_offset) * view.line_height()
    vx, _ = view.viewport_position()
    view.set_viewport_position((vx, vy))


def translate_row_to_inline_diff(diff_view, row):
    hunks = diff_view_hunks[diff_view.id()]
    deleted_lines_before_row = 0

    for hunk_ref in hunks:
        if hunk_ref.section_start > row + deleted_lines_before_row:
            break

        for type in hunk_ref.line_types:
            if type == "-":
                deleted_lines_before_row += 1

    return row + deleted_lines_before_row


def compute_identifier_for_view(view):
    # type: (sublime.View) -> Optional[Tuple]
    settings = view.settings()
    return (
        settings.get('git_savvy.repo_path'),
        settings.get('git_savvy.file_path'),
        settings.get('git_savvy.inline_diff_view.in_cached_mode'),
    ) if settings.get('git_savvy.inline_diff_view') else None


class gs_inline_diff(WindowCommand, GitCommand):

    """
    Given an open file in a git-tracked directory, show a new view with the
    diff (against HEAD) displayed inline.  Allow the user to stage or reset
    hunks or individual lines, and to navigate between hunks.
    """

    def run(self, settings=None, cached=False, match_current_position=False):
        if settings is None:
            file_view = self.window.active_view()
            assert file_view

            repo_path = self.repo_path
            file_path = self.file_path
            syntax_file = file_view.settings().get("syntax")
            cur_pos = capture_cur_position(file_view) if match_current_position else None

        else:
            repo_path = settings["repo_path"]
            file_path = settings["file_path"]
            syntax_file = settings["syntax"]
            cur_pos = None

        this_id = (repo_path, file_path, cached)
        for view in self.window.views():
            if compute_identifier_for_view(view) == this_id:
                diff_view = view
                focus_view(view)
                break

        else:
            diff_view = util.view.get_scratch_view(self, "inline_diff", read_only=True)

            settings = diff_view.settings()
            settings.set("git_savvy.repo_path", repo_path)
            settings.set("git_savvy.file_path", file_path)
            settings.set("git_savvy.inline_diff_view.in_cached_mode", cached)
            file_encoding = self.get_file_encoding(repo_path, file_path)
            if file_encoding:
                settings.set("git_savvy.inline_diff.encoding", file_encoding)

            title = INLINE_DIFF_CACHED_TITLE if cached else INLINE_DIFF_TITLE
            diff_view.set_name(title + os.path.basename(file_path))

            diff_view.set_syntax_file(syntax_file)

            diff_view.run_command("gs_handle_vintageous")

        diff_view.run_command("gs_inline_diff_refresh", {
            "match_position": cur_pos,
            "sync": True
        })

    def get_file_encoding(self, repo_path, file_path):
        # type: (str, str) -> Optional[str]
        file_binary = util.file.get_file_contents_binary(repo_path, file_path)
        try:
            file_binary.decode()
            return None
        except UnicodeDecodeError:
            try:
                file_binary.decode("latin-1")
                return "latin-1"
            except UnicodeDecodeError:
                return self.savvy_settings.get("fallback_encoding")


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

    If in `cached` mode, compare the file in the index againt the same file
    in the HEAD.  If a link or hunk is selected and the primary action for
    the view is taken, remove that line from the index.  Secondary actions
    are not supported in `cached` mode.
    """

    def run(self, edit, sync=True, match_position=None):
        # type: (sublime.Edit, bool, Optional[Tuple[int, int, float]]) -> None
        if sync:
            self._run(sync, match_position)
        else:
            sublime.set_timeout_async(lambda: self._run(sync, match_position))

    def _run(self, runs_on_ui_thread, match_position):
        # type: (bool, Optional[Tuple[int, int, float]]) -> None
        file_path = self.file_path
        in_cached_mode = self.view.settings().get("git_savvy.inline_diff_view.in_cached_mode")
        ignore_eol_ws = self.savvy_settings.get("inline_diff_ignore_eol_whitespaces", True)

        raw_diff = self.git(
            "diff",
            "--no-color",
            "-U0",
            "--ignore-space-at-eol" if ignore_eol_ws else None,
            "--cached" if in_cached_mode else None,
            "--",
            file_path
        )
        try:
            diff = util.parse_diff(raw_diff)
        except util.UnsupportedDiffMode:
            sublime.error_message("Inline-diff cannot be displayed for this file - "
                                  "it has a merge conflict.")
            self.view.close()
            return

        hunks_count = len(diff)
        if hunks_count == 0:
            flash(self.view, "The file is clean.")
            self.view.close()
            return
        else:
            flash(self.view, "File has {} {} {}".format(
                hunks_count,
                "staged" if in_cached_mode else "unstaged",
                "hunk" if hunks_count == 1 else "hunks"
            ))

        rel_file_path = self.get_rel_path(file_path).replace('\\', '/')
        if in_cached_mode:
            original_content = self.git("show", "HEAD:{}".format(rel_file_path))
        else:
            original_content = self.git("show", ":{}".format(rel_file_path))
        inline_diff_contents, replaced_lines = self.get_inline_diff_contents(original_content, diff)

        if runs_on_ui_thread:
            self.draw(self.view, match_position, inline_diff_contents, replaced_lines)
        else:
            enqueue_on_ui(self.draw, self.view, match_position, inline_diff_contents, replaced_lines)

    def draw(self, view, match_position, inline_diff_contents, replaced_lines):
        if match_position is None:
            cur_pos = capture_cur_position(view)

        replace_view_content(view, inline_diff_contents)

        if match_position is None:
            if cur_pos == (0, 0, 0) and self.savvy_settings.get("inline_diff_auto_scroll"):
                view.run_command("gs_inline_diff_navigate_hunk")
        else:
            row, col, row_offset = match_position
            new_row = translate_row_to_inline_diff(view, row)
            place_cursor_and_show(view, new_row, col, row_offset)

        self.highlight_regions(replaced_lines)

    def get_inline_diff_contents(self, original_contents, diff):
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
        hunks = []
        diff_view_hunks[self.view.id()] = hunks

        lines = original_contents.split("\n")
        replaced_lines = []

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
            replaced_lines.append((section_start, section_end, line_types, raw_lines))

            adjustment += len(diff_lines) - hunk.head_length

        return "\n".join(lines), replaced_lines

    def highlight_regions(self, replaced_lines):
        """
        Given an array of tuples, where each tuple contains the start and end
        of an inlined diff hunk as well as an array of line-types (add/remove)
        for the lines in that hunk, highlight the added regions in green and
        the removed regions in red.
        """
        add_regions = []
        add_bold_regions = []
        remove_regions = []
        remove_bold_regions = []

        for section_start, section_end, line_types, raw_lines in replaced_lines:
            region_start = None
            region_end = None
            region_type = None

            for type_index, line_number in enumerate(range(section_start, section_end)):
                line = self.view.full_line(self.view.text_point(line_number, 0))
                line_type = line_types[type_index]

                if not region_type:
                    region_type = line_type
                    region_start = line.begin()
                elif region_type != line_type:
                    region_end = line.begin()
                    list_ = add_regions if region_type == "+" else remove_regions
                    list_.append(sublime.Region(region_start, region_end))

                    region_type = line_type
                    region_start = line.begin()

            region_end = line.end()
            list_ = add_regions if region_type == "+" else remove_regions
            list_.append(sublime.Region(region_start, region_end))

            # If there are both additions and removals in the hunk, display additional
            # highlighting for the in-line changes (if similarity is above threshold).
            if "+" in line_types and "-" in line_types:
                # Determine start of hunk/section.
                section_start_idx = self.view.text_point(section_start, 0)

                # Removed lines come first in a hunk.
                remove_start = section_start_idx
                first_added_line = line_types.index("+")
                add_start = section_start_idx + len("\n".join(raw_lines[:first_added_line])) + 1

                removed_part = "\n".join(raw_lines[:first_added_line])
                added_part = "\n".join(raw_lines[first_added_line:])
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
            scope="diff.inserted.git-savvy.inline-diff"
        )
        self.view.add_regions(
            "git-savvy-removed-lines",
            remove_regions,
            scope="diff.deleted.git-savvy.inline-diff"
        )
        self.view.add_regions(
            "git-savvy-added-bold",
            add_bold_regions,
            scope="diff.inserted.char.git-savvy.inline-diff"
        )
        self.view.add_regions(
            "git-savvy-removed-bold",
            remove_bold_regions,
            scope="diff.deleted.char.git-savvy.inline-diff"
        )


class GsInlineDiffFocusEventListener(EventListener):

    """
    If the current view is an inline-diff view, refresh the view with
    latest file status when the view regains focus.
    """

    def on_activated(self, view):
        if view.settings().get("git_savvy.inline_diff_view") is True:
            view.run_command("gs_inline_diff_refresh", {"sync": False})


class gs_inline_diff_stage_or_reset_base(TextCommand, GitCommand):

    """
    Base class for any stage or reset operation in the inline-diff view.
    Determine the line number of the current cursor location, and use that
    to determine what diff to apply to the file (implemented in subclass).
    """

    def run(self, edit, **kwargs):
        sublime.set_timeout_async(lambda: self.run_async(**kwargs), 0)

    def run_async(self, reset=False):
        in_cached_mode = self.view.settings().get("git_savvy.inline_diff_view.in_cached_mode")
        ignore_ws = (
            "--ignore-whitespace"
            if self.savvy_settings.get("inline_diff_ignore_eol_whitespaces", True)
            else None
        )
        selections = self.view.sel()
        region = selections[0]
        # For now, only support staging selections of length 0.
        if len(selections) > 1 or not region.empty():
            return

        # Git lines are 1-indexed; Sublime rows are 0-indexed.
        line_number = self.view.rowcol(region.begin())[0] + 1
        diff_lines = self.get_diff_from_line(line_number, reset)

        rel_path = self.get_rel_path()
        if os.name == "nt":
            # Git expects `/`-delimited relative paths in diff.
            rel_path = rel_path.replace("\\", "/")
        header = DIFF_HEADER.format(path=rel_path)

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
        # NOTE: When in cached mode, the action taken will always be to apply
        #       the patch in reverse only to the index.

        args = [
            "apply",
            "--unidiff-zero",
            "--reverse" if (reset or in_cached_mode) else None,
            "--cached" if (not reset or in_cached_mode) else None,
            ignore_ws,
            "-"
        ]
        encoding = self.view.settings().get('git_savvy.inline_diff.encoding', 'UTF-8')

        self.git(*args, stdin=full_diff, stdin_encoding=encoding)
        self.save_to_history(args, full_diff, encoding)
        self.view.run_command("gs_inline_diff_refresh")

    def save_to_history(self, args, full_diff, encoding):
        """
        After successful `git apply`, save the apply-data into history
        attached to the view, for later Undo.
        """
        history = self.view.settings().get("git_savvy.inline_diff.history") or []
        history.append((args, full_diff, encoding))
        self.view.settings().set("git_savvy.inline_diff.history", history)


class gs_inline_diff_stage_or_reset_line(gs_inline_diff_stage_or_reset_base):

    """
    Given a line number, generate a diff of that single line in the active
    file, and apply that diff to the file.  If the `reset` flag is set to
    `True`, apply the patch in reverse (reverting that line to the version
    in HEAD).
    """

    def get_diff_from_line(self, line_no, reset):
        hunks = diff_view_hunks[self.view.id()]
        add_length_earlier_in_diff = 0
        cur_hunk_begin_on_minus = 0
        cur_hunk_begin_on_plus = 0

        # Find the correct hunk.
        for hunk_ref in hunks:
            if hunk_ref.section_start < line_no <= hunk_ref.section_end:
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
            return

        section_start = hunk_ref.section_start + 1

        # Determine head/staged starting line.
        index_in_hunk = line_no - section_start
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
        head_start = hunk_ref.hunk.head_start if line_type == "+" else hunk_ref.hunk.head_start + index_in_hunk

        if reset:
            xhead_start = head_start - index_in_hunk + (0 if line_type == "+" else add_length_earlier_in_diff)
            # xnew_start = head_start - cur_hunk_begin_on_minus + index_in_hunk + add_length_earlier_in_diff - 1

            return (
                "@@ -{head_start},{head_length} +{new_start},{new_length} @@\n"
                "{line_type}{line}").format(
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

        else:
            head_start += 1
            return (
                "@@ -{head_start},{head_length} +{new_start},{new_length} @@\n"
                "{line_type}{line}").format(
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


class gs_inline_diff_stage_or_reset_hunk(gs_inline_diff_stage_or_reset_base):

    """
    Given a line number, generate a diff of the hunk containing that line,
    and apply that diff to the file.  If the `reset` flag is set to `True`,
    apply the patch in reverse (reverting that hunk to the version in HEAD).
    """

    def get_diff_from_line(self, line_no, reset):
        hunks = diff_view_hunks[self.view.id()]
        add_length_earlier_in_diff = 0

        # Find the correct hunk.
        for hunk_ref in hunks:
            if hunk_ref.section_start < line_no <= hunk_ref.section_end:
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
            return

        stand_alone_header = \
            "@@ -{head_start},{head_length} +{new_start},{new_length} @@".format(
                head_start=hunk_ref.hunk.head_start + (add_length_earlier_in_diff if reset else 0),
                head_length=hunk_ref.hunk.head_length,
                # If head_length is zero, diff will report original start position
                # as one less than where the content is inserted, for example:
                #   @@ -75,0 +76,3 @@
                new_start=hunk_ref.hunk.head_start + (0 if hunk_ref.hunk.head_length else 1),
                new_length=hunk_ref.hunk.saved_length
            )

        return "\n".join([stand_alone_header] + hunk_ref.hunk.raw_lines[1:])


class gs_inline_diff_open_file(TextCommand):

    """
    Opens an editable view of the file being diff'd.
    """

    @util.view.single_cursor_coords
    def run(self, coords, edit):
        if not coords:
            return
        cursor_line, cursor_column = coords

        # Git lines/columns are 1-indexed; Sublime rows/columns are 0-indexed.
        row, col = self.get_editable_position(cursor_line + 1, cursor_column + 1)
        self.open_file(row, col)

    def open_file(self, row, col):
        file_name = self.view.settings().get("git_savvy.file_path")
        self.view.window().open_file(
            "{file}:{row}:{col}".format(
                file=file_name,
                row=row,
                col=col
            ),
            sublime.ENCODED_POSITION
        )

    def get_editable_position(self, line_no, col_no):
        hunk_ref = self.get_closest_hunk_ref_before(line_no)

        # No diff hunks exist before the selected line.
        if not hunk_ref:
            return line_no, col_no

        # The selected line is within the hunk.
        if hunk_ref.section_end >= line_no:
            hunk_change_index = line_no - hunk_ref.section_start - 1
            change = hunk_ref.hunk.changes[hunk_change_index]
            # If a removed line is selected, the cursor will be offset by non-existant
            # columns of the removed lines.  Therefore, move the cursor to column zero
            # when removed line is selected.
            return change.saved_pos, col_no if change.type == "+" else 0

        # The selected line is after the hunk.
        else:
            lines_after_hunk_end = line_no - hunk_ref.section_end - 1
            # Adjust line position for remove-only hunks.
            if all(change.type == "-" for change in hunk_ref.hunk.changes):
                lines_after_hunk_end += 1
            hunk_end_in_saved = hunk_ref.hunk.saved_start + hunk_ref.hunk.saved_length
            return hunk_end_in_saved + lines_after_hunk_end, col_no

    def get_closest_hunk_ref_before(self, line_no):
        hunks = diff_view_hunks[self.view.id()]
        for hunk_ref in reversed(hunks):
            if hunk_ref.section_start < line_no:
                return hunk_ref


class gs_inline_diff_navigate_hunk(GsNavigate):

    """
    Navigate to the next/previous hunk that appears after the current cursor
    position.
    """
    offset = 0

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

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        history = self.view.settings().get("git_savvy.inline_diff.history") or []
        if not history:
            return

        last_args, last_stdin, encoding = history.pop()
        # Toggle the `--reverse` flag.
        last_args[2] = "--reverse" if not last_args[2] else None

        self.git(*last_args, stdin=last_stdin, stdin_encoding=encoding)
        self.view.settings().set("git_savvy.inline_diff.history", history)

        self.view.run_command("gs_inline_diff_refresh")
