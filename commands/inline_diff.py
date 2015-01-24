import os
from xml.etree import ElementTree
from collections import namedtuple

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..common import messages, util
from .base_command import BaseCommand

HunkReference = namedtuple("HunkReference", ("section_start", "section_end", "hunk", "line_types", "lines"))

current_diff_view_hunks = None


class InlineDiffCommand(WindowCommand, BaseCommand):

    """
    Given an open file in a git-tracked directory, show a new view with the
    diff (against HEAD) displayed inline.
    """

    def run(self):
        file_view = self.window.active_view()
        title = messages.INLINE_DIFF_TITLE + os.path.basename(self.file_path)
        syntax_file = file_view.settings().get("syntax")
        original_color_scheme = file_view.settings().get("color_scheme")

        grafted_settings = {
            "git_better.file_path": self.file_path,
            "git_better.repo_path": self.repo_path
        }

        for view in self.window.views():
            if view.settings().get("git_better_view") == "inline_diff":
                diff_view = view
                break
        else:
            diff_view = self.window.new_file()
            diff_view.settings().set("git_better_view", "inline_diff")
            diff_view.set_scratch(True)
            diff_view.set_read_only(True)

        diff_view.settings().set("git_better_diff_view", True)
        diff_view.set_name(title)
        diff_view.set_syntax_file(syntax_file)
        self.augment_color_scheme(diff_view, original_color_scheme)
        for k, v in grafted_settings.items():
            diff_view.settings().set(k, v)

        self.window.focus_view(diff_view)

        diff_view.run_command("inline_diff_refresh")

    def augment_color_scheme(self, target_view, original_color_scheme):
        original_path = os.path.abspath(sublime.packages_path() + "/../" + original_color_scheme)

        with open(original_path, "rt", encoding="utf-8") as in_f:
            color_scheme_xml = in_f.read()

        plist = ElementTree.XML(color_scheme_xml)
        styles = plist.find("./dict/array")

        added_style = messages.ADDED_LINE_STYLE.format("37A832")
        removed_style = messages.REMOVED_LINE_STYLE.format("A83732")

        styles.append(ElementTree.XML(added_style))
        styles.append(ElementTree.XML(removed_style))

        if not os.path.exists(os.path.join(sublime.packages_path(), "User", "GitBetter")):
            os.makedirs(os.path.join(sublime.packages_path(), "User", "GitBetter"))

        augmented_style_path = os.path.join(sublime.packages_path(), "User", "GitBetter", "GitBetter.active-diff-view.tmTheme")

        with open(augmented_style_path, "wb") as out_f:
            out_f.write(messages.STYLES_HEADER.encode("utf-8"))
            out_f.write(ElementTree.tostring(plist, encoding="utf-8"))

        target_view.settings().set("color_scheme", "Packages/User/GitBetter/GitBetter.active-diff-view.tmTheme")


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

        cmd = self.git("diff", "-U0", head_staged_object, saved_object)
        if not cmd.success:
            return

        diff = util.parse_diff(cmd.stdout)

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
        cmd = self.git("ls-files", "-s", file_path)
        if not cmd.success:
            return

        # 100644 c9d70aa928a3670bc2b879b4a596f10d3e81ba7c 0   SomeFile.py
        #        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        git_file_entry = cmd.stdout.split(" ")
        return git_file_entry[1]

    def get_object_contents(self, object_hash):
        cmd = self.git("show", object_hash)
        return cmd.stdout if cmd.success else None

    def get_file_contents(self, file_path):
        with open(file_path, "rt", encoding="utf-8") as f:
            return f.read()

    def get_object_from_string(self, string):
        cmd = self.git("hash-object", "-w", "--stdin", stdin=string)
        return cmd.stdout.split("\n")[0] if cmd.success else None

    def get_inline_diff_contents(self, original_contents, diff):
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


class InlineDiffStageOrResetBase(TextCommand, BaseCommand):

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
        header = messages.DIFF_HEADER.format(path=filename)

        full_diff = header + diff_lines + "\n"
        reset_or_stage_flag = "-R" if reset else "--cached"
        cmd = self.git("apply", "--unidiff-zero", reset_or_stage_flag, "-", stdin=full_diff)
        print("which one?", reset_or_stage_flag)
        if cmd.success:
            cursor = self.view.sel()[0].begin()
            self.view.run_command("inline_diff_refresh", {"cursor": cursor})


class InlineDiffStageOrResetLineCommand(InlineDiffStageOrResetBase):

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

    def get_target_cursor_pos(self, current_line_number):
        for hunk_ref in current_diff_view_hunks:
            if hunk_ref.section_start > current_line_number:
                break

        return self.view.text_point(hunk_ref.section_start, 0)


class InlineDiffGotoPreviousHunk(InlineDiffGotoBase):

    def get_target_cursor_pos(self, current_line_number):
        previous_hunk_ref = None
        for hunk_ref in current_diff_view_hunks:
            if hunk_ref.section_end < current_line_number:
                previous_hunk_ref = hunk_ref

        if previous_hunk_ref:
            return self.view.text_point(previous_hunk_ref.section_start, 0)
