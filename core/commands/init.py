import os

import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util


NO_REPO_MESSAGE = ("It looks like you haven't initialized Git in this directory.  "
                   "Would you like to?")
REPO_PATH_PROMPT = "Enter root path of new git repo:"
CONFIRM_REINITIALIZE = ("It looks like Git is already initialized here.  "
                        "Would you like to re-initialize?")
NAME_MESSAGE = "Enter your first and last name:"
EMAIL_MESSAGE = "Enter your email address:"
NO_CONFIG_MESSAGE = ("It looks like you haven't configured Git yet.  Would you "
                     "like to enter your name and email for Git to use?")


class GsOfferInit(WindowCommand):

    """
    If a git command fails indicating no git repo was found, this
    command will ask the user whether they'd like to init a new repo.
    """

    def run(self):
        if sublime.ok_cancel_dialog(NO_REPO_MESSAGE):
            self.window.run_command("gs_init")


class GsInit(WindowCommand, GitCommand):

    """
    If the active Sublime window has folders added to the project (or if Sublime was
    opened from the terminal with something like `subl .`), initialize a new Git repo
    at that location.  If that directory cannot be determined, use the open file's
    directory.  If there is no open file, prompt the user for the directory to use.

    If the selected directory has previosly been initialized with Git, prompt the user
    to confirm a re-initialize before proceeding.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        open_folders = self.window.folders()
        if open_folders:
            suggested_git_root = open_folders[0]
        else:
            file_path = self.window.active_view().file_name()
            if file_path:
                suggested_git_root = os.path.dirname(file_path)
            else:
                suggested_git_root = ""

        if suggested_git_root and os.path.exists(os.path.join(suggested_git_root, ".git")):
            if sublime.ok_cancel_dialog(CONFIRM_REINITIALIZE):
                self.on_done(suggested_git_root, re_init=True)
            return

        self.window.show_input_panel(REPO_PATH_PROMPT, suggested_git_root, self.on_done, None, None)

    def on_done(self, path, re_init=False):
        self.git("init", working_dir=path)
        sublime.status_message("{word_start}nitialized repo successfully.".format(
            word_start="Re-i" if re_init else "I"))
        util.view.refresh_gitsavvy(self.window.active_view())


class GsSetupUserCommand(WindowCommand, GitCommand):

    """
    Set user's name and email address in global Git config.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        if sublime.ok_cancel_dialog(NO_CONFIG_MESSAGE, "OK"):
            self.get_name()

    def get_name(self):
        self.window.show_input_panel(NAME_MESSAGE, "", self.on_done_name, None, None)

    def on_done_name(self, name):
        self.git("config", "--global", "user.name", "\"{}\"".format(name))
        self.get_email()

    def get_email(self):
        self.window.show_input_panel(EMAIL_MESSAGE, "", self.on_done_email, None, None)

    def on_done_email(self, email):
        self.git("config", "--global", "user.email", "\"{}\"".format(email))
