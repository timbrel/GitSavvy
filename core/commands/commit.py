import os
import re

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..git_command import GitCommand


COMMIT_HELP_TEXT = """

## To make a commit, type your commit message and press {key}-ENTER. To cancel
## the commit, close the window.

## You may also reference or close a GitHub issue with this commit.  To do so,
## type `#` followed by the `tab` key.  You will be shown a list of issues
## related to the current repo.  You may also type `owner/repo#` plus the `tab`
## key to reference an issue in a different GitHub repo.
""".format(key="CTRL" if os.name == "nt" else "SUPER")

COMMIT_TITLE = "COMMIT"


class GsCommitCommand(WindowCommand, GitCommand):

    """
    Display a transient window to capture the user's desired commit message.
    If the user is amending the previous commit, pre-populate the commit
    message area with the previous commit message.
    """

    def run(self, repo_path=None, include_unstaged=False, amend=False):
        repo_path = repo_path or self.repo_path
        view = self.window.new_file()
        view.settings().set("git_savvy.get_long_text_view", True)
        view.settings().set("git_savvy.commit_view.include_unstaged", include_unstaged)
        view.settings().set("git_savvy.commit_view.amend", amend)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.set_syntax_file("Packages/GitSavvy/syntax/make_commit.tmLanguage")
        view.set_name(COMMIT_TITLE)
        view.set_scratch(True)
        view.run_command("gs_commit_initialize_view")


class GsCommitInitializeViewCommand(TextCommand, GitCommand):

    """
    Fill the view with the commit view help message, and optionally
    the previous commit message if amending.
    """

    def run(self, edit):
        merge_msg_path = os.path.join(self.repo_path, ".git", "MERGE_MSG")
        if self.view.settings().get("git_savvy.commit_view.amend"):
            last_commit_message = self.git("log", "-1", "--pretty=%B")
            initial_text = last_commit_message + COMMIT_HELP_TEXT
        elif os.path.exists(merge_msg_path):
            with open(merge_msg_path, "r") as f:
                initial_text = f.read() + COMMIT_HELP_TEXT
        else:
            initial_text = COMMIT_HELP_TEXT

        self.view.replace(edit, sublime.Region(0, 0), initial_text)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(0, 0))


class GsCommitViewDoCommitCommand(TextCommand, GitCommand):

    """
    Take the text of the current view (minus the help message text) and
    make a commit using the text for the commit message.
    """

    def run(self, edit):
        view_text = self.view.substr(sublime.Region(0, self.view.size()))
        commit_message = view_text.replace(COMMIT_HELP_TEXT, "")

        if self.view.settings().get("git_savvy.commit_view.include_unstaged"):
            self.add_all_tracked_files()

        if self.view.settings().get("git_savvy.commit_view.amend"):
            self.git("commit", "-q", "--amend", "-F", "-", stdin=commit_message)
        else:
            self.git("commit", "-q", "-F", "-", stdin=commit_message)

        self.view.window().focus_view(self.view)
        self.view.window().run_command("close_file")
