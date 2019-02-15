"""
Implements a special view to visualize and stage pieces of a project's
current diff.
"""

from contextlib import contextmanager
from itertools import dropwhile, takewhile
import os
import re
import bisect

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from .navigate import GsNavigate
from ..git_command import GitCommand
from ..exceptions import GitSavvyError
from ...common import util


DIFF_TITLE = "DIFF: {}"
DIFF_CACHED_TITLE = "DIFF (cached): {}"


diff_views = {}


class GsDiffCommand(WindowCommand, GitCommand):

    """
    Create a new view to display the difference of `target_commit`
    against `base_commit`. If `target_commit` is None, compare
    working directory with `base_commit`.  If `in_cached_mode` is set,
    display a diff of the Git index. Set `disable_stage` to True to
    disable Ctrl-Enter in the diff view.
    """

    def run(self, **kwargs):
        sublime.set_timeout_async(lambda: self.run_async(**kwargs), 0)

    def run_async(self, in_cached_mode=False, file_path=None, current_file=False, base_commit=None,
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
        else:
            diff_view = util.view.get_scratch_view(self, "diff", read_only=True)

            settings = diff_view.settings()
            settings.set("git_savvy.repo_path", repo_path)
            settings.set("git_savvy.file_path", file_path)
            settings.set("git_savvy.diff_view.in_cached_mode", in_cached_mode)
            settings.set("git_savvy.diff_view.ignore_whitespace", False)
            settings.set("git_savvy.diff_view.show_word_diff", False)
            settings.set("git_savvy.diff_view.base_commit", base_commit)
            settings.set("git_savvy.diff_view.target_commit", target_commit)
            settings.set("git_savvy.diff_view.show_diffstat", self.savvy_settings.get("show_diffstat", True))
            settings.set("git_savvy.diff_view.disable_stage", disable_stage)
            settings.set("git_savvy.diff_view.history", [])
            settings.set("git_savvy.diff_view.just_hunked", "")

            # Clickable lines:
            # (A)  common/commands/view_manipulation.py  |   1 +
            # (B) --- a/common/commands/view_manipulation.py
            # (C) +++ b/common/commands/view_manipulation.py
            # (D) diff --git a/common/commands/view_manipulation.py b/common/commands/view_manipulation.py
            #
            # Now the actual problem is that Sublime only accepts a subset of modern reg expressions,
            # B, C, and D are relatively straight forward because they match a whole line, and
            # basically all other lines in a diff start with one of `[+- ]`.
            FILE_RE = (
                r"^(?:\s(?=.*\s+\|\s+\d+\s)|--- a\/|\+{3} b\/|diff .+b\/)"
                #     ^^^^^^^^^^^^^^^^^^^^^ (A)
                #     ^ one space, and then somewhere later on the line the pattern `  |  23 `
                #                           ^^^^^^^ (B)
                #                                   ^^^^^^^^ (C)
                #                                            ^^^^^^^^^^^ (D)
                r"(\S[^|]*?)"
                #                    ^ ! lazy to not match the trailing spaces, see below

                r"(?:\s+\||$)"
                #          ^ (B), (C), (D)
                #    ^^^^^ (A) We must match the spaces here bc Sublime will not rstrip() the
                #    filename for us.
            )

            settings.set("result_file_regex", FILE_RE)
            # Clickable line:
            # @@ -69,6 +69,7 @@ class GsHandleVintageousCommand(TextCommand):
            #           ^^ we want the second (current) line offset of the diff
            settings.set("result_line_regex", r"^@@ [^+]*\+(\d+)")
            settings.set("result_base_dir", repo_path)

            if not title:
                title = (DIFF_CACHED_TITLE if in_cached_mode else DIFF_TITLE).format(
                    os.path.basename(file_path) if file_path else os.path.basename(repo_path)
                )
            diff_view.set_name(title)
            diff_view.set_syntax_file("Packages/GitSavvy/syntax/diff_view.sublime-syntax")
            diff_views[view_key] = diff_view

        self.window.focus_view(diff_view)
        diff_view.run_command("gs_diff_refresh")
        diff_view.run_command("gs_handle_vintageous")


class GsDiffRefreshCommand(TextCommand, GitCommand):

    """
    Refresh the diff view with the latest repo state.
    """

    def run(self, edit):
        if self.view.settings().get("git_savvy.disable_diff"):
            return
        in_cached_mode = self.view.settings().get("git_savvy.diff_view.in_cached_mode")
        ignore_whitespace = self.view.settings().get("git_savvy.diff_view.ignore_whitespace")
        show_word_diff = self.view.settings().get("git_savvy.diff_view.show_word_diff")
        base_commit = self.view.settings().get("git_savvy.diff_view.base_commit")
        target_commit = self.view.settings().get("git_savvy.diff_view.target_commit")
        show_diffstat = self.view.settings().get("git_savvy.diff_view.show_diffstat")
        disable_stage = self.view.settings().get("git_savvy.diff_view.disable_stage")

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

        if ignore_whitespace:
            prelude += "  IGNORING WHITESPACE\n"

        try:
            diff = self.git(
                "diff",
                "--ignore-all-space" if ignore_whitespace else None,
                "--word-diff" if show_word_diff else None,
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

        self.view.run_command(
            "gs_replace_view_text", {"text": text, "restore_cursors": True}
        )
        if not old_diff:
            self.view.run_command("gs_diff_navigate")


class GsDiffToggleSetting(TextCommand):

    """
    Toggle view settings: `ignore_whitespace` , or `show_word_diff`.
    """

    def run(self, edit, setting):
        settings = self.view.settings()

        setting_str = "git_savvy.diff_view.{}".format(setting)
        current_mode = settings.get(setting_str)
        next_mode = not current_mode
        settings.set(setting_str, next_mode)
        self.view.window().status_message("{} is now {}".format(setting, next_mode))

        self.view.run_command("gs_diff_refresh")


class GsDiffToggleCachedMode(TextCommand):

    """
    Toggle `in_cached_mode`.
    """

    # NOTE: MUST NOT be async, otherwise `view.show` will not update the view 100%!
    def run(self, edit):
        setting = 'in_cached_mode'

        if (
            self.view.settings().get("git_savvy.diff_view.base_commit")
            and self.view.settings().get("git_savvy.diff_view.target_commit")
        ):
            # There is no cached mode if you diff between two commits, so
            # we need to abort here
            return

        settings = self.view.settings()

        last_cursors = settings.get('git_savvy.diff_view.last_cursors') or []
        settings.set('git_savvy.diff_view.last_cursors', pickle_sel(self.view.sel()))

        setting_str = "git_savvy.diff_view.{}".format(setting)
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


def find_hunk_in_view(view, hunk):
    hunk_content = extract_first_hunk(hunk)
    if hunk_content:
        return (
            view.find(hunk_content[0], 0, sublime.LITERAL)
            or search_for_hunk_content_in_view(view, hunk_content[1:])
        )


def extract_first_hunk(hunk):
    hunk_lines = hunk.split('\n')
    not_hunk_start = lambda l: not l.startswith('@@ ')

    try:
        start, *rest = dropwhile(not_hunk_start, hunk_lines)
    except (StopIteration, ValueError):
        return None

    return [start] + list(takewhile(not_hunk_start, rest))


def search_for_hunk_content_in_view(view, lines):
    for hunk_content in shrink_list_sym(lines):
        region = view.find('\n'.join(hunk_content), 0, sublime.LITERAL)
        if region:
            return first_hunk_start_before_pt(view, region.a)


def first_hunk_start_before_pt(view, pt):
    for region in line_regions_before_pt(view, pt):
        if view.substr(region).startswith('@@ '):
            return region


def shrink_list_sym(list):
    while list:
        yield list
        list = list[1:-1]


def line_regions_before_pt(view, pt):
    row, _ = view.rowcol(pt)
    for row in reversed(range(row)):
        pt = view.text_point(row, 0)
        yield view.line(pt)


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


class GsDiffFocusEventListener(EventListener):

    """
    If the current view is a diff view, refresh the view with latest tree status
    when the view regains focus.
    """

    def on_activated(self, view):
        if view.settings().get("git_savvy.diff_view") is True:
            sublime.set_timeout_async(lambda: view.run_command("gs_diff_refresh"))


class GsDiffStageOrResetHunkCommand(TextCommand, GitCommand):

    """
    Depending on whether the user is in cached mode and what action
    the user took, either 1) stage, 2) unstage, or 3) reset the
    hunk under the user's cursor(s).
    """

    def run(self, edit, reset=False):
        ignore_whitespace = self.view.settings().get("git_savvy.diff_view.ignore_whitespace")
        show_word_diff = self.view.settings().get("git_savvy.diff_view.show_word_diff")
        if ignore_whitespace or show_word_diff:
            sublime.error_message("You have to be in a clean diff to stage.")
            return None

        # Filter out any cursors that are larger than a single point.
        cursor_pts = tuple(cursor.a for cursor in self.view.sel() if cursor.a == cursor.b)

        self.header_starts = tuple(region.a for region in self.view.find_all("^diff"))
        self.header_ends = tuple(region.b for region in self.view.find_all(r"^\+\+\+.+\n(?=@@)"))
        self.hunk_starts = tuple(region.a for region in self.view.find_all("^@@"))
        self.hunk_ends = sorted(list(
            # Hunks end when the next diff starts.
            set(self.header_starts[1:]) |
            # Hunks end when the next hunk starts, except for hunks
            # immediately following diff headers.
            (set(self.hunk_starts) - set(self.header_ends)) |
            # The last hunk ends at the end of the file.
            # It should include the last line (`+ 1`).
            set((self.view.size() + 1, ))
        ))

        sublime.set_timeout_async(lambda: self.apply_diffs_for_pts(cursor_pts, reset), 0)

    def apply_diffs_for_pts(self, cursor_pts, reset):
        in_cached_mode = self.view.settings().get("git_savvy.diff_view.in_cached_mode")

        # Apply the diffs in reverse order - otherwise, line number will be off.
        for pt in reversed(cursor_pts):
            hunk_diff = self.get_hunk_diff(pt)
            if not hunk_diff:
                return

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
                "-",
            )
            self.git(
                *args,
                stdin=hunk_diff
            )

            history = self.view.settings().get("git_savvy.diff_view.history")
            history.append((args, hunk_diff, pt, in_cached_mode))
            self.view.settings().set("git_savvy.diff_view.history", history)
            self.view.settings().set("git_savvy.diff_view.just_hunked", hunk_diff)

        sublime.set_timeout_async(lambda: self.view.run_command("gs_diff_refresh"))

    def get_hunk_diff(self, pt):
        """
        Given a cursor position, find and return the diff header and the
        diff for the selected hunk/file.
        """

        for hunk_start, hunk_end in zip(self.hunk_starts, self.hunk_ends):
            if hunk_start <= pt < hunk_end:
                break
        else:
            window = self.view.window()
            if window:
                window.status_message('Not within a hunk')
            return  # Error!

        header_start, header_end = max(
            (header_start, header_end)
            for header_start, header_end in zip(self.header_starts, self.header_ends)
            if (header_start, header_end) < (hunk_start, hunk_end)
        )

        header = self.view.substr(sublime.Region(header_start, header_end))
        diff = self.view.substr(sublime.Region(hunk_start, hunk_end))

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
            hunk_line_str = self.view.substr(self.view.line(hunk_start))
            hunk_line, _ = self.view.rowcol(hunk_start)
            cursor_line, _ = self.view.rowcol(cursor_pt)
            additional_lines = cursor_line - hunk_line - 1

            # Example: "diff --git a/src/js/main.spec.js b/src/js/main.spec.js" --> "src/js/main.spec.js"
            use_prepix = re.search(r" b/(.+?)$", diff_start_line)
            if use_prepix is None:
                filename = diff_start_line.split(" ")[-1]
            else:
                filename = use_prepix.groups()[0]

            # Example: "@@ -9,6 +9,7 @@" --> 9
            lineno = int(re.search(r"^@@ \-\d+(,-?\d+)? \+(\d+)", hunk_line_str).groups()[1])
            lineno = lineno + additional_lines

            self.load_file_at_line(filename, lineno)

    def load_file_at_line(self, filename, lineno):
        """
        Show file at target commit if `git_savvy.diff_view.target_commit` is non-empty.
        Otherwise, open the file directly.
        """
        target_commit = self.view.settings().get("git_savvy.diff_view.target_commit")
        full_path = os.path.join(self.repo_path, filename)
        if target_commit:
            self.view.window().run_command("gs_show_file_at_commit", {
                "commit_hash": target_commit,
                "filepath": full_path,
                "lineno": lineno
            })
        else:
            self.view.window().open_file(
                "{file}:{row}:{col}".format(file=full_path, row=lineno, col=0),
                sublime.ENCODED_POSITION
            )


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

        args, stdin, cursor, in_cached_mode = history.pop()
        # Toggle the `--reverse` flag.
        args[1] = "-R" if not args[1] else None

        self.git(*args, stdin=stdin)
        self.view.settings().set("git_savvy.diff_view.history", history)
        self.view.settings().set("git_savvy.diff_view.just_hunked", stdin)

        self.view.run_command("gs_diff_refresh")

        # The cursor is only applicable if we're still in the same cache/stage mode
        if self.view.settings().get("git_savvy.diff_view.in_cached_mode") == in_cached_mode:
            set_and_show_cursor(self.view, cursor)
