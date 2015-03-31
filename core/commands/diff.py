"""
Implements a special view to visualize and stage pieces of a project's
current diff.
"""

import os
import re
import bisect

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from ..git_command import GitCommand
from ...common import util


DIFF_TITLE = "DIFF: {}"
DIFF_CACHED_TITLE = "DIFF (cached): {}"


class GsDiffCommand(WindowCommand, GitCommand):

    """
    Create a new view to display the project's diff.  If `in_cached_mode` is set,
    display a diff of the Git index.
    """

    def run(self, **kwargs):
        sublime.set_timeout_async(lambda: self.run_async(**kwargs), 0)

    def run_async(self, in_cached_mode=False, file_path=None, current_file=False):
        repo_path = self.repo_path
        if current_file:
            file_path = self.file_path or file_path
        diff_view = util.view.get_read_only_view(self, "diff")
        title = (DIFF_CACHED_TITLE if in_cached_mode else DIFF_TITLE).format(os.path.basename(repo_path))
        diff_view.set_name(title)
        diff_view.set_syntax_file("Packages/Diff/Diff.tmLanguage")
        diff_view.settings().set("git_savvy.repo_path", repo_path)
        diff_view.settings().set("git_savvy.file_path", file_path)
        diff_view.settings().set("git_savvy.diff_view.in_cached_mode", in_cached_mode)
        self.window.focus_view(diff_view)
        diff_view.sel().clear()
        diff_view.run_command("gs_diff_refresh")


class GsDiffRefreshCommand(TextCommand, GitCommand):

    """
    Refresh the diff view with the latest repo state.
    """

    def run(self, edit, cursors=None):
        in_cached_mode = self.view.settings().get("git_savvy.diff_view.in_cached_mode")
        stdout = self.git("diff", "--cached" if in_cached_mode else None, self.file_path)

        self.view.run_command("gs_replace_view_text", {"text": stdout})


class GsDiffFocusEventListener(EventListener):

    """
    If the current view is a diff view, refresh the view with latest tree status
    when the view regains focus.
    """

    def on_activated(self, view):
        if view.settings().get("git_savvy.diff_view") == True:
            sublime.set_timeout_async(lambda: view.run_command("gs_diff_refresh"))


class GsDiffStageOrResetHunkCommand(TextCommand, GitCommand):

    """
    Depending on whether the user is in cached mode an what action
    the user took, either 1) stage, 2) unstage, or 3) reset the
    hunk under the user's cursor(s).
    """

    def run(self, edit, reset=False):
        # Filter out any cursors that are larger than a single point.
        cursor_pts = tuple(cursor.a for cursor in self.view.sel() if cursor.a == cursor.b)

        self.diff_starts = tuple(region.a for region in self.view.find_all("^diff"))
        self.diff_header_ends = tuple(region.b for region in self.view.find_all("^\+\+\+.+\n(?=@@)"))
        self.hunk_starts = tuple(region.a for region in self.view.find_all("^@@"))
        hunk_starts_following_headers = {region.b for region in self.view.find_all("^\+\+\+.+\n(?=@@)")}
        self.hunk_ends = sorted(list(
            # Hunks end when the next diff starts.
            set(self.diff_starts[1:]) |
            # Hunks end when the next hunk starts, except for hunks
            # immediately following diff headers.
            (set(self.hunk_starts) - hunk_starts_following_headers) |
            # The last hunk ends at the end of the file.
            set((self.view.size(), ))
            ))

        sublime.set_timeout_async(lambda: self.apply_diffs_for_pts(cursor_pts, reset), 0)

    def apply_diffs_for_pts(self, cursor_pts, reset):
        in_cached_mode = self.view.settings().get("git_savvy.diff_view.in_cached_mode")

        # Apply the diffs in reverse order - otherwise, line number will be off.
        for pt in reversed(cursor_pts):
            hunk_diff = self.get_hunk_diff(pt)

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

            self.git(
                "apply",
                "-R" if (reset or in_cached_mode) else None,
                "--cached" if (in_cached_mode or not reset) else None,
                "-",
                stdin=hunk_diff
            )

        sublime.set_timeout_async(lambda: self.view.run_command("gs_diff_refresh"))

    def get_hunk_diff(self, pt):
        """
        Given a cursor position, find and return the diff header and the
        diff for the selected hunk/file.
        """
        header_start = self.diff_starts[bisect.bisect(self.diff_starts, pt) - 1]
        header_end = self.diff_header_ends[bisect.bisect(self.diff_header_ends, pt) - 1]

        if not header_end or header_end < header_start:
            # The cursor is not within a hunk.
            return

        diff_start = self.hunk_starts[bisect.bisect(self.hunk_starts, pt) - 1]
        diff_end = self.hunk_ends[bisect.bisect(self.hunk_ends, pt)]

        header = self.view.substr(sublime.Region(header_start, header_end))
        diff = self.view.substr(sublime.Region(diff_start, diff_end))

        return header + diff


class GsDiffOpenFileAtHunkCommand(TextCommand, GitCommand):

    """
    For each cursor in the view, identify the hunk in which the cursor lies,
    and open the file at that hunk in a separate view.
    """

    def run(self, edit):
        # Filter out any cursors that are larger than a single point.
        cursor_pts = tuple(cursor.a for cursor in self.view.sel() if cursor.a == cursor.b)

        diff_starts = tuple(region.a for region in self.view.find_all("^diff"))
        hunk_starts = tuple(region.a for region in self.view.find_all("^@@"))

        for cursor_pt in cursor_pts:
            diff_start = diff_starts[bisect.bisect(diff_starts, cursor_pt) - 1]
            diff_start_line = self.view.substr(self.view.line(diff_start))
            hunk_start = hunk_starts[bisect.bisect(hunk_starts, cursor_pt) - 1]
            hunk_line = self.view.substr(self.view.line(hunk_start))

            # Example: "diff --git a/src/js/main.spec.js b/src/js/main.spec.js" --> "src/js/main.spec.js"
            filename = re.search(r" b/(.+?)$", diff_start_line).groups()[0]
            # Example: "@@ -9,6 +9,7 @@" --> 9
            lineno = int(re.search(r"^@@ \-\d+(,-?\d+)? \+(\d+)", hunk_line).groups()[1])

            self.load_file_at_line(filename, lineno)

    def load_file_at_line(self, filename, lineno):
        full_path = os.path.join(self.repo_path, filename)
        self.view.window().open_file("{file}:{row}:{col}".format(file=full_path, row=lineno, col=0), sublime.ENCODED_POSITION)
