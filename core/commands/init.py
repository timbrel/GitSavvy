import os

import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.input_panel import show_single_line_input_panel


NO_REPO_MESSAGE = ("It looks like you haven't initialized Git in this directory.  "
                   "Would you like to?")
REPO_PATH_PROMPT = "Enter root path of new git repo:"
CONFIRM_REINITIALIZE = ("It looks like Git is already initialized here.  "
                        "Would you like to re-initialize?")
NAME_MESSAGE = "Enter your first and last name:"
EMAIL_MESSAGE = "Enter your email address:"
NO_CONFIG_MESSAGE = ("It looks like you haven't configured Git yet.  Would you "
                     "like to enter your name and email for Git to use?")
RECLONE_CANT_BE_DONE = ("It looks like Git is already initialized here.  "
                        "You can not re-clone")
GIT_URL = "Enter git url:"


views_with_offer_made = set()


class GsOfferInit(WindowCommand, GitCommand):

    """
    If a git command fails indicating no git repo was found, this
    command will ask the user whether they'd like to init a new repo.

    Offer only once per session for a given view.
    """

    def run(self):
        if self.savvy_settings.get("disable_git_init_prompt"):
            return

        active_view_id = self.window.active_view().id()
        if active_view_id not in views_with_offer_made and sublime.ok_cancel_dialog(NO_REPO_MESSAGE):
            self.window.run_command("gs_init")
        else:
            views_with_offer_made.add(active_view_id)


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
        git_root = self.find_working_dir()

        if git_root and os.path.exists(os.path.join(git_root, ".git")):
            if sublime.ok_cancel_dialog(CONFIRM_REINITIALIZE):
                self.on_done(git_root, re_init=True)
            return

        show_single_line_input_panel(REPO_PATH_PROMPT, git_root, self.on_done, None, None)

    def on_done(self, path, re_init=False):
        self.git("init", working_dir=path)
        self.window.status_message("{word_start}nitialized repo successfully.".format(
            word_start="Re-i" if re_init else "I"))
        util.view.refresh_gitsavvy(self.window.active_view())


class GsClone(WindowCommand, GitCommand):

    """
    If the active Sublime window has folders added to the project (or if Sublime was
    opened from the terminal with something like `subl .`), initialize a new Git repo
    at that location.  If that directory cannot be determined, use the open file's
    directory.  If there is no open file, prompt the user for the directory to use.

    If the selected directory has previously been initialized with Git, prompt the user
    to confirm a re-initialize before proceeding.
    """

    def run(self, recursive=False):
        self.recursive = recursive
        show_single_line_input_panel(GIT_URL, '', self.on_enter_url, None, None)

    def on_enter_url(self, url):
        self.git_url = url
        self.suggested_git_root = self.find_suggested_git_root()
        show_single_line_input_panel(REPO_PATH_PROMPT, self.suggested_git_root, self.on_enter_directory, None, None)

    def find_suggested_git_root(self):
        folder = self.find_working_dir()
        project = self.project_name_from_url(self.git_url)
        if folder:
            if not os.path.exists(os.path.join(folder, project, ".git")):
                return os.path.join(folder, project)
            else:
                parent = os.path.realpath(os.path.join(folder, ".."))
                return os.path.join(parent, project)
        return ""

    def on_enter_directory(self, path):
        self.suggested_git_root = os.path.expanduser(path)  # handle ~/%HOME%
        if self.suggested_git_root and os.path.exists(os.path.join(self.suggested_git_root, ".git")):
            sublime.ok_cancel_dialog(RECLONE_CANT_BE_DONE)
            return

        sublime.set_timeout_async(self.do_clone, 0)

    def do_clone(self):
        self.window.status_message("Start cloning {}".format(self.git_url))
        self.git(
            "clone",
            "--recursive" if self.recursive else None,
            self.git_url,
            self.suggested_git_root,
            working_dir='.')
        self.window.status_message("Cloned repo successfully.")
        self.open_folder()
        util.view.refresh_gitsavvy(self.window.active_view())

    def open_folder(self):
        # taken from
        # https://github.com/rosshemsley/iOpener/blob/a35117a201290b63b53ba6372dbf8bbfc68f28b9/i_opener.py#L203-L205
        sublime.run_command("new_window")
        project_data = dict(folders=[dict(follow_symlinks=True, path=self.suggested_git_root)])
        sublime.active_window().set_project_data(project_data)


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
        show_single_line_input_panel(NAME_MESSAGE, "", self.on_done_name, None, None)

    def on_done_name(self, name):
        self.git("config", "--global", "user.name", "\"{}\"".format(name))
        self.get_email()

    def get_email(self):
        show_single_line_input_panel(EMAIL_MESSAGE, "", self.on_done_email, None, None)

    def on_done_email(self, email):
        self.git("config", "--global", "user.email", "\"{}\"".format(email))
