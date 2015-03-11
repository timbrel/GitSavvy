import os

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..git_command import GitCommand


COMMIT_HELP_TEXT = """

## To make a commit, type your commit message and press {key}-ENTER. To cancel
## the commit, close the window. To sign off the commit press {key}-S.

## You may also reference or close a GitHub issue with this commit.  To do so,
## type `#` followed by the `tab` key.  You will be shown a list of issues
## related to the current repo.  You may also type `owner/repo#` plus the `tab`
## key to reference an issue in a different GitHub repo.

""".format(key="CTRL" if os.name == "nt" else "SUPER")

COMMIT_SIGN_TEXT = """

Signed-off-by: {name} <{email}>
"""

COMMIT_TITLE = "COMMIT"


class GsCommitCommand(WindowCommand, GitCommand):

    """
    Display a transient window to capture the user's desired commit message.
    If the user is amending the previous commit, pre-populate the commit
    message area with the previous commit message.
    """

    def run(self, **kwargs):
        sublime.set_timeout_async(lambda: self.run_async(**kwargs), 0)

    def run_async(self, repo_path=None, include_unstaged=False, amend=False):
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

        option_amend = self.view.settings().get("git_savvy.commit_view.amend")
        if option_amend:
            last_commit_message = self.git("log", "-1", "--pretty=%B")
            initial_text = last_commit_message + COMMIT_HELP_TEXT
        elif os.path.exists(merge_msg_path):
            with open(merge_msg_path, "r") as f:
                initial_text = f.read() + COMMIT_HELP_TEXT
        else:
            initial_text = COMMIT_HELP_TEXT

        if sublime.load_settings("GitSavvy.sublime-settings").get("show_commit_diff"):
            if option_amend:
                initial_text += self.git("diff", "HEAD^")
            else:
                initial_text += self.git("diff", "--cached")

        self.view.run_command("gs_replace_view_text", {
            "text": initial_text,
            "nuke_cursors": True
            })


class GsCommitViewDoCommitCommand(TextCommand, GitCommand):

    """
    Take the text of the current view (minus the help message text) and
    make a commit using the text for the commit message.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        view_text = self.view.substr(sublime.Region(0, self.view.size()))
        commit_message = view_text.split(COMMIT_HELP_TEXT)[0]

        if self.view.settings().get("git_savvy.commit_view.include_unstaged"):
            self.add_all_tracked_files()

        show_panel_overrides = \
            sublime.load_settings("GitSavvy.sublime-settings").get("show_panel_for")

        self.git(
            "commit",
            "-q" if "commit" not in show_panel_overrides else None,
            "--amend" if self.view.settings().get("git_savvy.commit_view.amend") else None,
            "-F",
            "-",
            stdin=commit_message
            )

        self.view.window().focus_view(self.view)
        self.view.window().run_command("close_file")


class GsCommitViewSignCommand(TextCommand, GitCommand):

    """
    Sign off on the commit with full name and email.
    """

    def run(self, edit):
        view_text = self.view.substr(sublime.Region(0, self.view.size()))
        view_text_list = view_text.split(COMMIT_HELP_TEXT)

        config_name = self.git("config", "user.name").strip()
        config_email = self.git("config", "user.email").strip()

        sign_text = COMMIT_SIGN_TEXT.format(name=config_name, email=config_email)
        view_text_list[0] += sign_text

        self.view.run_command("gs_replace_view_text", {
            "text": COMMIT_HELP_TEXT.join(view_text_list),
            "nuke_cursors": True
            })
