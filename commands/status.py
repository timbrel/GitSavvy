import os
from functools import partial

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from .base_command import BaseCommand
from ..common import util

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
  REMOTE:    {remote_info}
  LOCAL:     {local_info}
  INFO:      Your branch is {branch_info}.
"""

KEY_BINDINGS_MENU = """
  ###################                   ###############
  ## SELECTED FILE ##                   ## ALL FILES ##
  ###################                   ###############

  [o] open file                         [a] stage all unstaged files
  [s] stage file                        [A] stage all unstaged and untracked files
  [u] unstage file                      [U] unstage all staged files
  [d] discard file                      [D] discard all unstaged changes
  [h] open file on remote
  [R] reset file to HEAD
  [M] resolve conflict with Sublimerge

  [f] diff file                         [F] diff all files
  [l] diff file inline

  #############                         #############
  ## ACTIONS ##                         ## STASHES ##
  #############                         #############

  [c] commit                            [t][a] apply stash
  [C] commit, including unstaged        [t][p] pop stash
  [m] amend previous commit             [t][c] create stash
                                        [t][C] create stash including untracked files
  [i] ignore file                       [t][d] discard stash
  [I] ignore pattern

  ###########
  ## OTHER ##
  ###########

  [r] refresh status

-
"""

MERGE_CONFLICT_PORCELAIN_STATUSES = (
    ("D", "D"),  # unmerged, both deleted
    ("A", "U"),  # unmerged, added by us
    ("U", "D"),  # unmerged, deleted by them
    ("U", "A"),  # unmerged, added by them
    ("D", "U"),  # unmerged, deleted by us
    ("A", "A"),  # unmerged, both added
    ("U", "U")  # unmerged, both modified
)

status_view_section_ranges = {}


class GgShowStatusCommand(WindowCommand, BaseCommand):

    """
    Open a status view for the active git repository.
    """

    def run(self):
        repo_path = self.repo_path
        title = STATUS_TITLE.format(os.path.basename(repo_path))
        status_view = self.get_read_only_view("status")
        status_view.set_name(title)
        status_view.set_syntax_file("Packages/GitGadget/syntax/status.tmLanguage")
        status_view.settings().set("git_gadget.repo_path", repo_path)
        self.window.focus_view(status_view)
        status_view.sel().clear()

        status_view.run_command("gg_status_refresh")


class GgStatusRefreshCommand(TextCommand, BaseCommand):

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
        header = STATUS_HEADER_TEMPLATE.format(
            remote_info="unimplemented",
            local_info="unimplemented",
            branch_info="unimplemented",
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
            unstaged_lines = "\n".join("    " + f.path for f in unstaged)
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
            staged_lines = "\n".join("    " + f.path for f in staged)
            staged_text = STAGED_TEMPLATE.format(staged_lines)
            staged_region = get_region(staged_text)
            status_text += staged_text

        contents = header + status_text + KEY_BINDINGS_MENU

        return contents, (unstaged_region, conflicts_region, untracked_region, staged_region)

    @staticmethod
    def sort_status_entries(file_status_list):
        staged, unstaged, untracked, conflicts = [], [], [], []

        for f in file_status_list:
            if f.index_status == "?":
                untracked.append(f)
            elif (f.index_status, f.working_status) in MERGE_CONFLICT_PORCELAIN_STATUSES:
                conflicts.append(f)
            elif f.working_status in ("M", "D"):
                unstaged.append(f)
            else:
                staged.append(f)

        return staged, unstaged, untracked, conflicts


class GgStatusFocusEventListener(EventListener):

    """
    If the current view is an inline-diff view, refresh the view with
    latest file status when the view regains focus.
    """

    def on_activated(self, view):

        if view.settings().get("git_gadget.status_view") == True:
            view.run_command("gg_status_refresh")


class GgStatusOpenFileCommand(TextCommand, BaseCommand):

    def run(self, edit):
        lines = util.get_lines_from_regions(self.view, self.view.sel())
        file_paths = (line.strip() for line in lines if line[:4] == "    ")
        abs_paths = (os.path.join(self.repo_path, file_path) for file_path in file_paths)
        for path in abs_paths:
            self.view.window().open_file(path)


class GgStatusDiffInlineCommand(TextCommand, BaseCommand):

    def run(self, edit):
        lines = util.get_lines_from_regions(self.view, self.view.sel())
        file_paths = (line.strip() for line in lines if line[:4] == "    ")
        sublime.set_timeout_async(partial(self.load_inline_diff_windows, file_paths), 0)

    def load_inline_diff_windows(self, file_paths):
        for fpath in file_paths:
            syntax = util.get_syntax_for_file(fpath)
            settings = {
                "git_gadget.file_path": fpath,
                "git_gadget.repo_path": self.repo_path,
                "syntax": syntax
            }
            self.view.window().run_command("gg_inline_diff", {"settings": settings})


class GgStatusStageFileCommand(TextCommand, BaseCommand):

    def run(self, edit):
        # Valid selections are in the Unstaged, Untracked, and Conflicts sections.
        valid_ranges = status_view_section_ranges[self.view.id()][:3]

        lines = util.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=valid_ranges
        )
        file_paths = (line.strip() for line in lines if line[:4] == "    ")

        if file_paths:
            for fpath in file_paths:
                self.stage_file(fpath)
            sublime.status_message("Staged files successfully.")
            self.view.run_command("gg_status_refresh")
