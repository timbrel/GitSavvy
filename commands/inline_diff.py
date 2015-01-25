import os
from collections import namedtuple

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from ..common import util
from ..common.theme_generator import ThemeGenerator
from .base_command import BaseCommand

HunkReference = namedtuple("HunkReference", ("section_start", "section_end", "hunk", "line_types", "lines"))


INLINE_DIFF_TITLE = "DIFF: "

DIFF_HEADER = """diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
"""


current_diff_view_hunks = None


class InlineDiffCommand(WindowCommand, BaseCommand):

    """
    Given an open file in a git-tracked directory, show a new view with the
    diff (against HEAD) displayed inline.  Allow the user to stage or reset
    hunks or individual lines, and to navigate between hunks.
    """

    def run(self):
        file_view = self.window.active_view()
        title = INLINE_DIFF_TITLE + os.path.basename(self.file_path)
        syntax_file = file_view.settings().get("syntax")
        original_color_scheme = file_view.settings().get("color_scheme")

        grafted_settings = {
            "git_better.file_path": self.file_path,
            "git_better.repo_path": self.repo_path
        }

        diff_view = self.get_diff_view()
        diff_view.settings().set("git_better.inline_diff_view", True)
        diff_view.set_name(title)
        diff_view.set_syntax_file(syntax_file)
        self.augment_color_scheme(diff_view, original_color_scheme)
        for k, v in grafted_settings.items():
            diff_view.settings().set(k, v)

        self.window.focus_view(diff_view)

        diff_view.run_command("inline_diff_refresh")

    def get_diff_view(self):
        """
        Return a read-only diff view.  If one already exists, return that.
        Otherwise, return a new view.
        """
        for view in self.window.views():
            if view.settings().get("git_better_view") == "inline_diff":
                break
        else:
            view = self.window.new_file()
            view.settings().set("git_better_view", "inline_diff")
            view.set_scratch(True)
            view.set_read_only(True)

        return view

    def augment_color_scheme(self, target_view, original_color_scheme):
        """
        Given the path to a color scheme and a target view, generate a new
        color scheme from the original with additional inline-diff-related
        style rules added.  Save this color scheme to disk and set it as
        the target view's active color scheme.
        """
        themeGenerator = ThemeGenerator(original_color_scheme)
        themeGenerator.add_scoped_style("GitBetter Added Line", "gitbetter.change.addition", background="#37A832")
        themeGenerator.add_scoped_style("GitBetter Removed Line", "gitbetter.change.removal", background="#A83732")
        themeGenerator.apply_new_theme("active-diff-view", target_view)


class InlineDiffRefreshCommand(TextCommand, BaseCommand):

    """
    Diff the original view's file against the HEAD or staged version of that
    file.  Remove regions of the staged/head version where changes have occurred
    and replace them with added/removed lines from the diff.  Highlight added
    and removed lines with different colors.
    """

    def run(self, edit, cursor=None):
        file_path = self.file_path
        head_staged_object = self.get_file_object_hash(file_path)
        head_staged_object_contents = self.get_object_contents(head_staged_object)
        saved_file_contents = self.get_file_contents(file_path)
        saved_object = self.get_object_from_string(saved_file_contents)

        stdout = self.git("diff", "-U0", head_staged_object, saved_object)

        diff = util.parse_diff(stdout)

        inline_diff_contents, replaced_lines = \
            self.get_inline_diff_contents(head_staged_object_contents, diff)

        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), inline_diff_contents)
        self.view.sel().clear()
        if cursor is not None:
            pt = sublime.Region(cursor, cursor)
            self.view.sel().add(pt)
            # Sublime seems to scroll right when a `-` line is removed from
            # the diff view.  As a work around, center and show the beginning
            # of the line that the user was on when staging a line.
            self.view.show_at_center(self.view.line(pt).begin())
        self.highlight_regions(replaced_lines)
        self.view.set_read_only(True)

    def get_file_object_hash(self, file_path):
        """
        Given an absolute path to a file contained in a git repo, return
        git's internal object hash associated with the version of that file
        in the index (if the file is staged) or in the HEAD (if it is not
        staged).
        """
        stdout = self.git("ls-files", "-s", file_path)

        # 100644 c9d70aa928a3670bc2b879b4a596f10d3e81ba7c 0   SomeFile.py
        #        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        git_file_entry = stdout.split(" ")
        return git_file_entry[1]

    def get_object_contents(self, object_hash):
        """
        Given the object hash to a versioned object in the current git repo,
        display the contents of that object.
        """
        return self.git("show", object_hash)

    def get_file_contents(self, file_path):
        """
        Given an absolute file path, return the text contents of that file
        as a string.
        """
        with open(file_path, "rt", encoding="utf-8") as f:
            return f.read()

    def get_object_from_string(self, string):
        """
        Given a string, pipe the contents of that string to git and have it
        stored in the current repo, and return an object-hash that can be
        used to diff against.
        """
        stdout = self.git("hash-object", "-w", "--stdin", stdin=string)
        return stdout.split("\n")[0]

    def get_inline_diff_contents(self, original_contents, diff):
        """
        Given a file's original contents and an array of hunks that could be
        applied to it, return a string with the diff lines inserted inline.
        Also return an array of inlined-hunk information to be used for
        diff highlighting.

        Remove any `-` or `+` characters at the beginning of each line, as
        well as the header summary line.  Additionally, store relevant data
        as `current_diff_view_hunks` to be used when the user takes an
        action in the view.
        """
        global current_diff_view_hunks
        current_diff_view_hunks = []

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
            current_diff_view_hunks.append(HunkReference(
                section_start, section_end, hunk, line_types, raw_lines
            ))

            # Discard the first character of every diff-line (`+`, `-`).
            lines = lines[:section_start] + raw_lines + lines[head_end + adjustment:]
            replaced_lines.append((section_start, section_end, line_types))

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
        remove_regions = []

        for section_start, section_end, line_types in replaced_lines:

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
                    # End region with final character of previous line.
                    region_end = line.begin() - 1
                    l = add_regions if region_type == "+" else remove_regions
                    l.append(sublime.Region(region_start, region_end))

                    region_type = line_type
                    region_start = line.begin()

            region_end = line.end()
            l = add_regions if region_type == "+" else remove_regions
            l.append(sublime.Region(region_start, region_end))

        self.view.add_regions("git-better-added-lines", add_regions, scope="gitbetter.change.addition")
        self.view.add_regions("git-better-removed-lines", remove_regions, scope="gitbetter.change.removal")


class InlineDiffFocusEventListener(EventListener):

    """
    If the current view is an inline-diff view, refresh the view with
    latest file status when the view regains focus.
    """

    def on_activated(self, view):

        if view.settings().get("git_better.inline_diff_view") == True:
            view.run_command("inline_diff_refresh")


class InlineDiffStageOrResetBase(TextCommand, BaseCommand):

    """
    Base class for any stage or reset operation in the inline-diff view.
    Determine the line number of the current cursor location, and use that
    to determine what diff to apply to the file (implemented in subclass).
    """

    def run(self, edit, reset=False):
        selections = self.view.sel()
        region = selections[0]
        # For now, only support staging selections of length 0.
        if len(selections) > 1 or not region.empty():
            return

        # Git lines are 1-indexed; Sublime rows are 0-indexed.
        line_number = self.view.rowcol(region.begin())[0] + 1
        diff_lines = self.get_diff_from_line(line_number, reset)
        filename = os.path.relpath(self.file_path, self.repo_path)
        header = DIFF_HEADER.format(path=filename)

        full_diff = header + diff_lines + "\n"
        reset_or_stage_flag = "-R" if reset else "--cached"

        self.git("apply", "--unidiff-zero", reset_or_stage_flag, "-", stdin=full_diff)
        cursor = self.view.sel()[0].begin()
        self.view.run_command("inline_diff_refresh", {"cursor": cursor})


class InlineDiffStageOrResetLineCommand(InlineDiffStageOrResetBase):

    """
    Given a line number, generate a diff of that single line in the active
    file, and apply that diff to the file.  If the `reset` flag is set to
    `True`, apply the patch in reverse (reverting that line to the version
    in HEAD).
    """

    @staticmethod
    def get_diff_from_line(line_no, reset):
        # Find the correct hunk.
        for hunk_ref in current_diff_view_hunks:
            if hunk_ref.section_start <= line_no and hunk_ref.section_end >= line_no:
                break
        # Correct hunk not found.
        else:
            return

        section_start = hunk_ref.section_start + 1

        # Determine head/staged starting line.
        index_in_hunk = line_no - section_start
        line = hunk_ref.lines[index_in_hunk]
        line_type = hunk_ref.line_types[index_in_hunk]

        # Removed lines are always first with `git diff -U0 ...`. Therefore, the
        # line to remove will be the Nth line, where N is the line index in the hunk.
        head_start = hunk_ref.hunk.head_start if line_type == "+" else hunk_ref.hunk.head_start + index_in_hunk
        # TODO: Investigate this off-by-one ???
        if not reset:
            head_start += 1

        return ("@@ -{head_start},{head_length} +{new_start},{new_length} @@\n"
                "{line_type}{line}").format(
                    head_start=head_start,
                    head_length="0" if line_type == "+" else "1",
                    new_start=head_start,
                    new_length="1" if line_type == "+" else "0",
                    line_type=line_type,
                    line=line
                )


class InlineDiffStageOrResetHunkCommand(InlineDiffStageOrResetBase):

    """
    Given a line number, generate a diff of the hunk containing that line,
    and apply that diff to the file.  If the `reset` flag is set to `True`,
    apply the patch in reverse (reverting that hunk to the version in HEAD).
    """

    @staticmethod
    def get_diff_from_line(line_no, reset):
        # Find the correct hunk.
        for hunk_ref in current_diff_view_hunks:
            if hunk_ref.section_start <= line_no and hunk_ref.section_end >= line_no:
                break
        # Correct hunk not found.
        else:
            return

        return "\n".join(hunk_ref.hunk.raw_lines)


class InlineDiffGotoBase(TextCommand):

    """
    Base class for navigation commands in the inline-diff view.  Determine
    the current line number, get a new target cursor position (implemented
    in subclass), make that the only cursor active in the view, and center
    it on the screen.
    """

    def run(self, edit):
        selections = self.view.sel()
        region = self.view.line(0) if len(selections) == 0 else selections[0]

        # Git lines are 1-indexed; Sublime rows are 0-indexed.
        current_line_number = self.view.rowcol(region.begin())[0] + 1

        new_cursor_pt = self.get_target_cursor_pos(current_line_number)
        if new_cursor_pt:
            self.view.sel().clear()
            self.view.sel().add(new_cursor_pt)
            self.view.show_at_center(self.view.line(new_cursor_pt))


class InlineDiffGotoNextHunk(InlineDiffGotoBase):

    """
    Navigate to the next hunk that appears after the current cursor
    position.
    """

    def get_target_cursor_pos(self, current_line_number):
        for hunk_ref in current_diff_view_hunks:
            if hunk_ref.section_start > current_line_number:
                break

        return self.view.text_point(hunk_ref.section_start, 0)


class InlineDiffGotoPreviousHunk(InlineDiffGotoBase):

    """
    Navigate to the previous hunk that appears immediately before
    the current cursor position.
    """

    def get_target_cursor_pos(self, current_line_number):
        previous_hunk_ref = None
        for hunk_ref in current_diff_view_hunks:
            if hunk_ref.section_end < current_line_number:
                previous_hunk_ref = hunk_ref

        if previous_hunk_ref:
            return self.view.text_point(previous_hunk_ref.section_start, 0)
