import os
from collections import namedtuple

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from ..common import util
from ..common.theme_generator import ThemeGenerator
from .base_command import BaseCommand

HunkReference = namedtuple("HunkReference", ("section_start", "section_end", "hunk", "line_types", "lines"))


INLINE_DIFF_TITLE = "DIFF: "
INLINE_DIFF_CACHED_TITLE = "DIFF (cached): "

DIFF_HEADER = """diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
"""


diff_view_hunks = {}


class GgInlineDiffCommand(WindowCommand, BaseCommand):

    """
    Given an open file in a git-tracked directory, show a new view with the
    diff (against HEAD) displayed inline.  Allow the user to stage or reset
    hunks or individual lines, and to navigate between hunks.
    """

    def run(self, settings=None, cached=False):
        if settings is None:
            file_view = self.window.active_view()
            syntax_file = file_view.settings().get("syntax")
            settings = {
                "git_gadget.file_path": self.file_path,
                "git_gadget.repo_path": self.repo_path
            }
        else:
            syntax_file = settings["syntax"]
            del settings["syntax"]

        diff_view = self.get_read_only_view("inline_diff")
        title = INLINE_DIFF_CACHED_TITLE if cached else INLINE_DIFF_TITLE
        diff_view.set_name(title + os.path.basename(settings["git_gadget.file_path"]))

        diff_view.set_syntax_file(syntax_file)
        file_ext = util.get_file_extension(os.path.basename(settings["git_gadget.file_path"]))
        self.augment_color_scheme(diff_view, file_ext)

        diff_view.settings().set("git_gadget.inline_diff.cached", cached)
        for k, v in settings.items():
            diff_view.settings().set(k, v)

        self.window.focus_view(diff_view)

        diff_view.run_command("gg_inline_diff_refresh")

    def augment_color_scheme(self, target_view, file_ext):
        """
        Given a target view, generate a new color scheme from the original with
        additional inline-diff-related style rules added.  Save this color scheme
        to disk and set it as the target view's active color scheme.
        """
        original_color_scheme = target_view.settings().get("color_scheme")
        themeGenerator = ThemeGenerator(original_color_scheme)
        themeGenerator.add_scoped_style("GitGadget Added Line", "git_gadget.change.addition", background="#37A832")
        themeGenerator.add_scoped_style("GitGadget Removed Line", "git_gadget.change.removal", background="#A83732")
        themeGenerator.apply_new_theme("active-diff-view." + file_ext, target_view)


class GgInlineDiffRefreshCommand(TextCommand, BaseCommand):

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

    def run(self, edit, cursor=None):
        file_path = self.file_path
        in_cached_mode = self.view.settings().get("git_gadget.inline_diff.cached")

        if in_cached_mode:
            indexed_object = self.get_indexed_file_object(file_path)
            head_file_object = self.get_head_file_object(file_path)
            head_file_contents = self.get_object_contents(head_file_object)

            # Display the changes introduced between HEAD and index.
            stdout = self.git("diff", "-U0", head_file_object, indexed_object)
            diff = util.parse_diff(stdout)
            inline_diff_contents, replaced_lines = \
                self.get_inline_diff_contents(head_file_contents, diff)
        else:
            indexed_object = self.get_indexed_file_object(file_path)
            indexed_object_contents = self.get_object_contents(indexed_object)
            working_tree_file_contents = self.get_file_contents(file_path)
            working_tree_file_object = self.get_object_from_string(working_tree_file_contents)

            # Display the changes introduced between index and working dir.
            stdout = self.git("diff", "-U0", indexed_object, working_tree_file_object)
            diff = util.parse_diff(stdout)
            inline_diff_contents, replaced_lines = \
                self.get_inline_diff_contents(indexed_object_contents, diff)

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

    def get_indexed_file_object(self, file_path):
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

    def get_head_file_object(self, file_path):
        """
        Given an absolute path to a file contained in a git repo, return
        git's internal object hash associated with the version of that
        file in the HEAD.
        """
        stdout = self.git("ls-tree", "HEAD", file_path)

        # 100644 blob 7317069f30eafd4d7674612679322d59f9fb65a4    SomeFile.py
        #             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        git_file_entry = stdout.split()  # split by spaces and tabs
        return git_file_entry[2]

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
        file_path = os.path.join(self.repo_path, file_path)
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

        self.view.add_regions("git-better-added-lines", add_regions, scope="git_gadget.change.addition")
        self.view.add_regions("git-better-removed-lines", remove_regions, scope="git_gadget.change.removal")


class GgInlineDiffFocusEventListener(EventListener):

    """
    If the current view is an inline-diff view, refresh the view with
    latest file status when the view regains focus.
    """

    def on_activated(self, view):

        if view.settings().get("git_gadget.inline_diff_view") == True:
            view.run_command("gg_inline_diff_refresh")


class GgInlineDiffStageOrResetBase(TextCommand, BaseCommand):

    """
    Base class for any stage or reset operation in the inline-diff view.
    Determine the line number of the current cursor location, and use that
    to determine what diff to apply to the file (implemented in subclass).
    """

    def run(self, edit, reset=False):
        in_cached_mode = self.view.settings().get("git_gadget.inline_diff.cached")
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

        # The three argument combinations below result from the following
        # three scenarios:
        #
        # 1) The user is in non-cached mode and wants to stage a line/hunk, so
        #    do NOT apply the patch in reverse, but do apply it only against
        #    the cached/indexed file (not the working tree).
        # 2) The user is in non-cached mode and wants to undo a line/hunk, so
        #    DO apply the patch in reverse, and do apply it both against the
        #    index and the working tree.
        # 3) The user is in cached mode and wants to undo a line hunk, so DO
        #    apply the patch in reverse, but only apply it against the cached/
        #    indexed file.
        #
        # NOTE: When in cached mode, the action taken will always be to apply
        #       the patch in reverse only to the index.

        args = [
            "apply",
            "--unidiff-zero",
            "-R" if (reset or in_cached_mode) else None,
            "--cached" if (not reset or in_cached_mode) else None,
            "-"
        ]

        self.git(*args, stdin=full_diff)
        cursor = self.view.sel()[0].begin()
        self.view.run_command("gg_inline_diff_refresh", {"cursor": cursor})


class GgInlineDiffStageOrResetLineCommand(GgInlineDiffStageOrResetBase):

    """
    Given a line number, generate a diff of that single line in the active
    file, and apply that diff to the file.  If the `reset` flag is set to
    `True`, apply the patch in reverse (reverting that line to the version
    in HEAD).
    """

    def get_diff_from_line(self, line_no, reset):
        hunks = diff_view_hunks[self.view.id()]

        # Find the correct hunk.
        for hunk_ref in hunks:
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


class GgInlineDiffStageOrResetHunkCommand(GgInlineDiffStageOrResetBase):

    """
    Given a line number, generate a diff of the hunk containing that line,
    and apply that diff to the file.  If the `reset` flag is set to `True`,
    apply the patch in reverse (reverting that hunk to the version in HEAD).
    """

    def get_diff_from_line(self, line_no, reset):
        hunks = diff_view_hunks[self.view.id()]

        # Find the correct hunk.
        for hunk_ref in hunks:
            if hunk_ref.section_start <= line_no and hunk_ref.section_end >= line_no:
                break
        # Correct hunk not found.
        else:
            return

        return "\n".join(hunk_ref.hunk.raw_lines)


class GgInlineDiffGotoBase(TextCommand):

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


class GgInlineDiffGotoNextHunk(GgInlineDiffGotoBase):

    """
    Navigate to the next hunk that appears after the current cursor
    position.
    """

    def get_target_cursor_pos(self, current_line_number):
        hunks = diff_view_hunks[self.view.id()]
        if not hunks:
            return
        for hunk_ref in hunks:
            if hunk_ref.section_start > current_line_number:
                break

        return self.view.text_point(hunk_ref.section_start, 0)


class GgInlineDiffGotoPreviousHunk(GgInlineDiffGotoBase):

    """
    Navigate to the previous hunk that appears immediately before
    the current cursor position.
    """

    def get_target_cursor_pos(self, current_line_number):
        hunks = diff_view_hunks[self.view.id()]
        previous_hunk_ref = None
        for hunk_ref in hunks:
            if hunk_ref.section_end < current_line_number:
                previous_hunk_ref = hunk_ref

        if previous_hunk_ref:
            return self.view.text_point(previous_hunk_ref.section_start, 0)
