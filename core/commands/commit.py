import os

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..git_command import GitCommand
from ...common import util


COMMIT_HELP_TEXT_EXTRA = """##
## You may also reference or close a GitHub issue with this commit.  To do so,
## type `#` followed by the `tab` key.  You will be shown a list of issues
## related to the current repo.  You may also type `owner/repo#` plus the `tab`
## key to reference an issue in a different GitHub repo.

"""

COMMIT_HELP_TEXT_ALT = """

## To make a commit, type your commit message and close the window. To cancel
## the commit, delete the commit message and close the window. To sign off on
## the commit, press {key}-S.
""".format(key=util.super_key) + COMMIT_HELP_TEXT_EXTRA


COMMIT_HELP_TEXT = """

## To make a commit, type your commit message and press {key}-ENTER. To cancel
## the commit, close the window. To sign off on the commit, press {key}-S.
""".format(key=util.super_key) + COMMIT_HELP_TEXT_EXTRA

COMMIT_SIGN_TEXT = """

Signed-off-by: {name} <{email}>
"""

COMMIT_TITLE = "COMMIT: {}"


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
        settings = view.settings()
        settings.set("git_savvy.get_long_text_view", True)
        settings.set("git_savvy.commit_view", True)
        settings.set("git_savvy.commit_view.include_unstaged", include_unstaged)
        settings.set("git_savvy.commit_view.amend", amend)
        settings.set("git_savvy.repo_path", repo_path)

        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        if savvy_settings.get("use_syntax_for_commit_editmsg"):
            syntax_file = util.file.get_syntax_for_file("COMMIT_EDITMSG")
            view.set_syntax_file(syntax_file)
        else:
            view.set_syntax_file("Packages/GitSavvy/syntax/make_commit.sublime-syntax")

        view.run_command("gs_handle_vintageous")

        commit_on_close = savvy_settings.get("commit_on_close")
        settings.set("git_savvy.commit_on_close", commit_on_close)

        title = COMMIT_TITLE.format(os.path.basename(repo_path))
        view.set_name(title)
        if commit_on_close or not savvy_settings.get("prompt_on_abort_commit"):
            view.set_scratch(True)
        view.run_command("gs_commit_initialize_view")


class GsCommitInitializeViewCommand(TextCommand, GitCommand):

    """
    Fill the view with the commit view help message, and optionally
    the previous commit message if amending.
    """

    def run(self, edit):
        merge_msg_path = os.path.join(self.repo_path, ".git", "MERGE_MSG")
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")

        help_text = (COMMIT_HELP_TEXT_ALT
                     if savvy_settings.get("commit_on_close")
                     else COMMIT_HELP_TEXT)
        self.view.settings().set("git_savvy.commit_view.help_text", help_text)

        option_amend = self.view.settings().get("git_savvy.commit_view.amend")
        if option_amend:
            last_commit_message = self.git("log", "-1", "--pretty=%B").strip()
            initial_text = last_commit_message + help_text
        elif os.path.exists(merge_msg_path):
            with util.file.safe_open(merge_msg_path, "r") as f:
                initial_text = f.read() + help_text
        else:
            initial_text = help_text

        commit_help_extra_file = savvy_settings.get("commit_help_extra_file") or ".commit_help"
        commit_help_extra_path = os.path.join(self.repo_path, commit_help_extra_file)
        if os.path.exists(commit_help_extra_path):
            with util.file.safe_open(commit_help_extra_path, "r", encoding="utf-8") as f:
                initial_text += f.read()

        show_commit_diff = savvy_settings.get("show_commit_diff")
        if show_commit_diff == "stat":
            initial_text += self.git(
                "diff",
                "--stat",
                "--no-color",
                "--cached",
                "HEAD^" if option_amend else None
            )
        elif show_commit_diff == "full" or show_commit_diff is True:
            initial_text += self.git(
                "diff",
                "--no-color",
                "--cached",
                "HEAD^" if option_amend else None
            )

        self.view.run_command("gs_replace_view_text", {
            "text": initial_text,
            "nuke_cursors": True
            })


class GsCommitViewDoCommitCommand(TextCommand, GitCommand):

    """
    Take the text of the current view (minus the help message text) and
    make a commit using the text for the commit message.
    """

    def run(self, edit, message=None):
        sublime.set_timeout_async(lambda: self.run_async(commit_message=message), 0)

    def run_async(self, commit_message=None):
        if commit_message is None:
            view_text = self.view.substr(sublime.Region(0, self.view.size()))
            help_text = self.view.settings().get("git_savvy.commit_view.help_text")
            commit_message = view_text.split(help_text)[0]

        include_unstaged = self.view.settings().get("git_savvy.commit_view.include_unstaged")

        show_panel_overrides = \
            sublime.load_settings("GitSavvy.sublime-settings").get("show_panel_for")

        self.git(
            "commit",
            "-q" if "commit" not in show_panel_overrides else None,
            "-a" if include_unstaged else None,
            "--amend" if self.view.settings().get("git_savvy.commit_view.amend") else None,
            "-F",
            "-",
            stdin=commit_message
            )

        # ensure view is not already closed (i.e.: when "commit_on_close" enabled)
        is_commit_view = self.view.settings().get("git_savvy.commit_view")
        if is_commit_view and self.view.window():
            self.view.window().focus_view(self.view)
            self.view.set_scratch(True)  # ignore dirty on actual commit
            self.view.window().run_command("close_file")
        else:
            sublime.set_timeout_async(
                lambda: util.view.refresh_gitsavvy(sublime.active_window().active_view()))


class GsCommitViewSignCommand(TextCommand, GitCommand):

    """
    Sign off on the commit with full name and email.
    """

    def run(self, edit):
        view_text = self.view.substr(sublime.Region(0, self.view.size()))
        help_text = self.view.settings().get("git_savvy.commit_view.help_text")
        view_text_list = view_text.split(help_text)

        config_name = self.git("config", "user.name").strip()
        config_email = self.git("config", "user.email").strip()

        sign_text = COMMIT_SIGN_TEXT.format(name=config_name, email=config_email)
        view_text_list[0] += sign_text

        self.view.run_command("gs_replace_view_text", {
            "text": help_text.join(view_text_list),
            "nuke_cursors": True
            })


class GsCommitViewCloseCommand(TextCommand, GitCommand):

    """
    Perform commit action on commit view close if `commit_on_close` setting
    is enabled.
    """

    def run(self, edit):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        if savvy_settings.get("commit_on_close"):
            view_text = self.view.substr(sublime.Region(0, self.view.size()))
            help_text = self.view.settings().get("git_savvy.commit_view.help_text")
            message_txt = (view_text.split(help_text)[0]
                           if help_text in view_text
                           else "")
            message_txt = message_txt.strip()

            if message_txt:
                self.view.run_command("gs_commit_view_do_commit", {"message": message_txt})
