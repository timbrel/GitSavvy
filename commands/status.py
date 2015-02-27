import os
from functools import partial

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from .base_command import BaseCommand
from ..common import util
from ..common.constants import MERGE_CONFLICT_PORCELAIN_STATUSES

STATUS_TITLE = "STATUS: {}"

STAGED_TEMPLATE = """
  STAGED:
{}
"""

UNSTAGED_TEMPLATE = """
  UNSTAGED:
{}
"""

UNTRACKED_TEMPLATE = """
  UNTRACKED:
{}
"""

MERGE_CONFLICTS_TEMPLATE = """
  MERGE CONFLICTS:
{}
"""

STASHES_TEMPLATE = """
  STASHES:
{}
"""

STATUS_HEADER_TEMPLATE = """
  BRANCH:  {branch_status}
  ROOT:    {repo_root}
"""

NO_STATUS_MESSAGE = """
  Your working directory is clean.
"""

KEY_BINDINGS_MENU = """
  ###################                   ###############
  ## SELECTED FILE ##                   ## ALL FILES ##
  ###################                   ###############

  [o] open file                         [a] stage all unstaged files
  [s] stage file                        [A] stage all unstaged and untracked files
  [u] unstage file                      [U] unstage all staged files
  [d] discard changes to file           [D] discard all unstaged changes
  [h] open file on remote
  [M] resolve conflict with Sublimerge

  [l] diff file inline                  [f] diff all files
                                        [F] diff all cached files

  #############                         #############
  ## ACTIONS ##                         ## STASHES ##
  #############                         #############

  [c] commit                            [t][a] apply stash
  [C] commit, including unstaged        [t][p] pop stash
  [m] amend previous commit             [t][s] show stash
                                        [t][c] create stash
  [i] ignore file                       [t][u] create stash including untracked files
  [I] ignore pattern                    [t][d] discard stash

  ###########
  ## OTHER ##
  ###########

  [r] refresh status

-
"""

status_view_section_ranges = {}


class GsShowStatusCommand(WindowCommand, BaseCommand):

    """
    Open a status view for the active git repository.
    """

    def run(self):
        repo_path = self.repo_path
        title = STATUS_TITLE.format(os.path.basename(repo_path))
        status_view = self.get_read_only_view("status")
        status_view.set_name(title)
        status_view.set_syntax_file("Packages/GitSavvy/syntax/status.tmLanguage")
        status_view.settings().set("git_savvy.repo_path", repo_path)
        status_view.settings().set("word_wrap", False)
        self.window.focus_view(status_view)
        status_view.sel().clear()

        status_view.run_command("gs_status_refresh")


class GsStatusRefreshCommand(TextCommand, BaseCommand):

    """
    Get the current state of the git repo and display file status
    and command menu to the user.
    """

    def run(self, edit, cursor=None):
        status_contents, ranges = self.get_contents()
        status_view_section_ranges[self.view.id()] = ranges

        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), status_contents)
        self.view.set_read_only(True)

        selections = self.view.sel()
        if cursor is not None:
            selections.clear()
            pt = sublime.Region(cursor, cursor)
            selections.add(pt)
        elif not len(selections):
            pt = sublime.Region(0, 0)
            selections.add(pt)

    def get_contents(self):
        """
        Get the branch status and use that information to build the text to
        display to the user.
        """
        header = STATUS_HEADER_TEMPLATE.format(
            branch_status=self.get_branch_status(),
            repo_root=self.repo_path
        )

        cursor = len(header)
        staged, unstaged, untracked, conflicts = self.sort_status_entries(self.get_status())
        unstaged_region, conflicts_region, untracked_region, staged_region = (sublime.Region(0, 0), ) * 4

        def get_region(new_text):
            nonlocal cursor
            start = cursor
            cursor += len(new_text)
            end = cursor
            return sublime.Region(start, end)

        status_text = ""

        if unstaged:
            unstaged_lines = "\n".join(
                "  {} {}".format("-" if f.working_status == "D" else " ", f.path)
                for f in unstaged
                )
            unstaged_text = UNSTAGED_TEMPLATE.format(unstaged_lines)
            unstaged_region = get_region(unstaged_text)
            status_text += unstaged_text
        if conflicts:
            conflicts_lines = "\n".join("    " + f.path for f in conflicts)
            conflicts_text = MERGE_CONFLICTS_TEMPLATE.format(conflicts_lines)
            conflicts_region = get_region(conflicts_text)
            status_text += conflicts_text
        if untracked:
            untracked_lines = "\n".join("    " + f.path for f in untracked)
            untracked_text = UNTRACKED_TEMPLATE.format(untracked_lines)
            untracked_region = get_region(untracked_text)
            status_text += untracked_text
        if staged:
            staged_lines = "\n".join(
                "  {} {}".format("-" if f.index_status == "D" else " ", f.path)
                for f in staged
                )
            staged_text = STAGED_TEMPLATE.format(staged_lines)
            staged_region = get_region(staged_text)
            status_text += staged_text

        status_text = status_text or NO_STATUS_MESSAGE

        contents = header + status_text + self.get_stashes_contents() + KEY_BINDINGS_MENU

        return contents, (unstaged_region, conflicts_region, untracked_region, staged_region)

    def get_stashes_contents(self):
        """
        Get a list of stashes to display to the user.
        """
        stash_list = self.get_stashes()
        if not stash_list:
            return ""

        stash_lines = ("    ({}) {}".format(stash.id, stash.description) for stash in stash_list)

        return STASHES_TEMPLATE.format("\n".join(stash_lines))

    @staticmethod
    def sort_status_entries(file_status_list):
        """
        Take entries from `git status` and sort them into groups.
        """
        staged, unstaged, untracked, conflicts = [], [], [], []

        for f in file_status_list:
            if (f.index_status, f.working_status) in MERGE_CONFLICT_PORCELAIN_STATUSES:
                conflicts.append(f)
                continue
            if f.index_status == "?":
                untracked.append(f)
                continue
            elif f.working_status in ("M", "D"):
                unstaged.append(f)
            if f.index_status != " ":
                staged.append(f)

        return staged, unstaged, untracked, conflicts


class GsStatusFocusEventListener(EventListener):

    """
    If the current view is an inline-diff view, refresh the view with
    latest file status when the view regains focus.
    """

    def on_activated(self, view):

        if view.settings().get("git_savvy.status_view") == True:
            view.run_command("gs_status_refresh")


class GsStatusOpenFileCommand(TextCommand, BaseCommand):

    """
    For every file that is selected or under a cursor, open a that
    file in a new view.
    """

    def run(self, edit):
        lines = util.view.get_lines_from_regions(self.view, self.view.sel())
        file_paths = (line.strip() for line in lines if line[:4] == "    ")
        abs_paths = (os.path.join(self.repo_path, file_path) for file_path in file_paths)
        for path in abs_paths:
            self.view.window().open_file(path)


class GsStatusDiffInlineCommand(TextCommand, BaseCommand):

    """
    For every file selected or under a cursor, open a new inline-diff view for
    that file.  If the file is staged, open the inline-diff in cached mode.
    """

    def run(self, edit):
        # Unstaged, Untracked, and Conflicts
        non_cached_sections = status_view_section_ranges[self.view.id()][:3]
        non_cached_lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=non_cached_sections
        )
        non_cached_files = (
            os.path.join(self.repo_path, line.strip())
            for line in non_cached_lines
            if line[:4] == "    ")

        # Staged
        cached_sections = status_view_section_ranges[self.view.id()][3:]
        cached_lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=cached_sections
        )
        cached_files = (
            os.path.join(self.repo_path, line.strip())
            for line in cached_lines
            if line[:4] == "    ")

        sublime.set_timeout_async(
            partial(self.load_inline_diff_windows, non_cached_files, cached_files), 0)

    def load_inline_diff_windows(self, non_cached_files, cached_files):
        for fpath in non_cached_files:
            syntax = util.file.get_syntax_for_file(fpath)
            settings = {
                "git_savvy.file_path": fpath,
                "git_savvy.repo_path": self.repo_path,
                "syntax": syntax
            }
            self.view.window().run_command("gs_inline_diff", {"settings": settings})

        for fpath in cached_files:
            syntax = util.file.get_syntax_for_file(fpath)
            settings = {
                "git_savvy.file_path": fpath,
                "git_savvy.repo_path": self.repo_path,
                "syntax": syntax
            }
            self.view.window().run_command("gs_inline_diff", {
                "settings": settings,
                "cached": True
            })


class GsStatusStageFileCommand(TextCommand, BaseCommand):

    """
    For every file that is selected or under a cursor, if that file is
    unstaged, stage it.
    """

    def run(self, edit):
        # Valid selections are in the Unstaged, Untracked, and Conflicts sections.
        valid_ranges = status_view_section_ranges[self.view.id()][:3]

        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
        )
        # Remove the leading spaces and hyphen-character for deleted files.
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            for fpath in file_paths:
                self.stage_file(fpath)
            sublime.status_message("Staged files successfully.")
            self.view.run_command("gs_status_refresh")


class GsStatusUnstageFileCommand(TextCommand, BaseCommand):

    """
    For every file that is selected or under a cursor, if that file is
    staged, unstage it.
    """

    def run(self, edit):
        # Valid selections are only in the Staged section.
        valid_ranges = (status_view_section_ranges[self.view.id()][3], )
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
        )
        # Remove the leading spaces and hyphen-character for deleted files.
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            for fpath in file_paths:
                self.unstage_file(fpath)
            sublime.status_message("Unstaged files successfully.")
            self.view.run_command("gs_status_refresh")


class GsStatusDiscardChangesToFileCommand(TextCommand, BaseCommand):

    """
    For every file that is selected or under a cursor, if that file is
    unstaged, reset the file to HEAD.
    """

    def run(self, edit):
        # Valid selections are in the Unstaged, Untracked, and Conflicts sections.
        valid_ranges = status_view_section_ranges[self.view.id()][:3]

        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
        )
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            for fpath in file_paths:
                self.checkout_file(fpath)
            sublime.status_message("Successfully checked out files from HEAD.")
            self.view.run_command("gs_status_refresh")


class GsStatusOpenFileOnRemoteCommand(TextCommand, BaseCommand):

    """
    For every file that is selected or under a cursor, open a new browser
    window to that file on GitHub.
    """

    def run(self, edit):
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=status_view_section_ranges[self.view.id()]
        )
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            file_paths = list(file_paths)
            for fpath in file_paths:
                self.open_file_on_remote(fpath)


class GsStatusStageAllFilesCommand(TextCommand, BaseCommand):

    """
    Stage all unstaged files.
    """

    def run(self, edit):
        self.add_all_tracked_files()
        self.view.run_command("gs_status_refresh")


class GsStatusStageAllFilesWithUntrackedCommand(TextCommand, BaseCommand):

    """
    Stage all unstaged files, including new files.
    """

    def run(self, edit):
        self.add_all_files()
        self.view.run_command("gs_status_refresh")


class GsStatusUnstageAllFilesCommand(TextCommand, BaseCommand):

    """
    Unstage all staged changes.
    """

    def run(self, edit):
        self.unstage_all_files()
        self.view.run_command("gs_status_refresh")


class GsStatusDiscardAllChangesCommand(TextCommand, BaseCommand):

    """
    Reset all unstaged files to HEAD.
    """

    def run(self, edit):
        self.discard_all_unstaged()
        self.view.run_command("gs_status_refresh")


class GsStatusCommitCommand(TextCommand, BaseCommand):

    """
    Open a commit window.
    """

    def run(self, edit):
        self.view.window().run_command("gs_commit", {"repo_path": self.repo_path})


class GsStatusCommitUnstagedCommand(TextCommand, BaseCommand):

    """
    Open a commit window.  When the commit message is provided, stage all unstaged
    changes and then do the commit.
    """

    def run(self, edit):
        self.view.window().run_command(
            "gs_commit",
            {"repo_path": self.repo_path, "include_unstaged": True}
        )


class GsStatusAmendCommand(TextCommand, BaseCommand):

    """
    Open a commit window to amend the previous commit.
    """

    def run(self, edit):
        self.view.window().run_command(
            "gs_commit",
            {"repo_path": self.repo_path, "amend": True}
        )


class GsStatusIgnoreFileCommand(TextCommand, BaseCommand):

    """
    For each file that is selected or under a cursor, add an
    entry to the git root's `.gitignore` file.
    """

    def run(self, edit):
        # Valid selections are only in the Staged section.
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=status_view_section_ranges[self.view.id()]
        )
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            for fpath in file_paths:
                self.add_ignore(os.path.join("/", fpath))
            sublime.status_message("Successfully ignored files.")
            self.view.run_command("gs_status_refresh")


class GsStatusIgnorePatternCommand(TextCommand, BaseCommand):

    """
    For the first file that is selected or under a cursor (other
    selections/cursors will be ignored), prompt the user for
    a new pattern to `.gitignore`, prefilled with the filename.
    """

    def run(self, edit):
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=status_view_section_ranges[self.view.id()]
        )
        file_paths = tuple(line[4:].strip() for line in lines if line)

        if file_paths:
            self.view.window().run_command("gs_ignore_pattern", {"pre_filled": file_paths[0]})


class GsStatusApplyStashCommand(TextCommand, BaseCommand):

    """
    Apply the selected stash.  The user can only apply one stash at a time.
    """

    def run(self, edit):
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel()
        )
        ids = tuple(line[line.find("(")+1:line.find(")")] for line in lines if line)

        if len(ids) > 1:
            sublime.status_message("You can only apply one stash at a time.")
            return

        self.apply_stash(ids[0])
        self.view.run_command("gs_status_refresh")


class GsStatusPopStashCommand(TextCommand, BaseCommand):

    """
    Pop the selected stash.  The user can only pop one stash at a time.
    """

    def run(self, edit):
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel()
        )
        ids = tuple(line[line.find("(")+1:line.find(")")] for line in lines if line)

        if len(ids) > 1:
            sublime.status_message("You can only pop one stash at a time.")
            return

        self.pop_stash(ids[0])
        self.view.run_command("gs_status_refresh")


class GsStatusShowStashCommand(TextCommand, BaseCommand):

    """
    For each selected stash, open a new window to display the diff
    for that stash.
    """

    def run(self, edit):
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel()
        )
        ids = tuple(line[line.find("(")+1:line.find(")")] for line in lines if line)

        for stash_id in ids:
            stash_name = "stash@{{{}}}".format(stash_id)
            stash_text = self.git("stash", "show", "-p", stash_name)
            stash_view = self.get_stash_view(stash_name)
            stash_view.set_read_only(False)
            stash_view.replace(edit, sublime.Region(0, 0), stash_text)
            stash_view.set_read_only(True)
            self.view.sel().add(sublime.Region(0, 0))

    def get_stash_view(self, title):
        window = self.window if hasattr(self, "window") else self.view.window()
        repo_path = self.repo_path
        stash_view = self.get_read_only_view("stash_" + title)
        stash_view.set_name(title)
        stash_view.set_syntax_file("Packages/Diff/Diff.tmLanguage")
        stash_view.settings().set("git_savvy.repo_path", repo_path)
        window.focus_view(stash_view)
        stash_view.sel().clear()

        return stash_view


class GsStatusCreateStashCommand(TextCommand, BaseCommand):

    """
    Create a new stash from the user's unstaged changes.
    """

    def run(self, edit):
        self.view.window().show_input_panel("Description:", "", self.on_done, None, None)

    def on_done(self, description):
        self.create_stash(description)
        self.view.run_command("gs_status_refresh")


class GsStatusCreateStashWithUntrackedCommand(TextCommand, BaseCommand):

    """
    Create a new stash from the user's unstaged changes, including
    new files.
    """

    def run(self, edit):
        self.view.window().show_input_panel("Description:", "", self.on_done, None, None)

    def on_done(self, description):
        self.create_stash(description, include_untracked=True)
        self.view.run_command("gs_status_refresh")


class GsStatusDiscardStashCommand(TextCommand, BaseCommand):

    """
    Drop the selected stash.  The user can only discard one stash
    at a time.
    """

    def run(self, edit):
        lines = util.view.get_lines_from_regions(
            self.view,
            self.view.sel()
        )
        ids = tuple(line[line.find("(")+1:line.find(")")] for line in lines if line)

        if len(ids) > 1:
            sublime.status_message("You can only drop one stash at a time.")
            return

        self.drop_stash(ids[0])
        self.view.run_command("gs_status_refresh")
