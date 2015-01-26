import os

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from .base_command import BaseCommand

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

STATUS_TEMPLATE = """## GIT STATUS ##

  REMOTE:    {remote_info}
  LOCAL:     {local_info}
  INFO:      Your branch is {branch_info}.

{status_text}

  # = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = = #

  ## SELECTED FILE ##                   ## ALL FILES ##

  [o] open file                         [a] stage all unstaged files
  [s] stage file                        [A] stage all unstaged and untracked files
  [u] unstage file                      [U] unstage all staged files
  [d] discard file                      [D] discard all unstaged changes
  [h] open file on GitHub
  [R] reset file to HEAD

  [f] diff file                         [F] diff all files
  [l] diff file inline

  ## ACTIONS ##                         ## STASHES ##

  [c] commit                            [t][a] apply stash
  [C] commit, including unstaged        [t][p] pop stash
  [m] amend previous commit             [t][c] create stash
                                        [t][C] create stash including untracked files
  [i] ignore file                       [t][d] discard stash
  [I] ignore pattern

  ## OTHER ##

  [r] refresh status
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


class GgShowStatusCommand(WindowCommand, BaseCommand):

    """
    Open a status view for the active git repository.
    """

    def run(self):
        repo_path = self.repo_path
        title = STATUS_TITLE.format(os.path.basename(repo_path))
        status_view = self.get_read_only_view("status")
        status_view.set_name(title)
        status_view.set_syntax_file("Packages/GitGadget/GitGadgetSyntax.tmLanguage")
        status_view.settings().set("git_gadget.repo_path", repo_path)
        self.window.focus_view(status_view)

        status_view.run_command("gg_status_refresh")


class GgStatusRefreshCommand(TextCommand, BaseCommand):

    """
    Get the current state of the git repo and display file status
    and command menu to the user.
    """

    def run(self, edit, cursor=None):
        status_contents = self.get_contents()

        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), status_contents)
        self.view.set_read_only(True)

        self.view.sel().clear()
        if cursor is not None:
            pt = sublime.Region(cursor, cursor)
            self.view.sel().add(pt)

    def get_contents(self):
        staged, unstaged, untracked, conflicts = self.sort_status_entries(self.get_status())

        status_text = ""

        if unstaged:
            unstaged_lines = "\n".join("    " + f.path for f in unstaged)
            status_text += UNSTAGED_TEMPLATE.format(unstaged_lines)
        if conflicts:
            conflicts_lines = "\n".join("    " + f.path for f in conflicts)
            status_text += MERGE_CONFLICTS_TEMPLATE.format(conflicts_lines)
        if untracked:
            untracked_lines = "\n".join("    " + f.path for f in untracked)
            status_text += UNTRACKED_TEMPLATE.format(untracked_lines)
        if staged:
            staged_lines = "\n".join("    " + f.path for f in staged)
            status_text += STAGED_TEMPLATE.format(staged_lines)

        return STATUS_TEMPLATE.format(
            remote_info="unimplemented",
            local_info="unimplemented",
            branch_info="unimplemented",
            status_text=status_text
        )

        return status_text

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

        if view.settings().get("git_gadget.inline_diff_view") == True:
            view.run_command("gg_status_refresh")
