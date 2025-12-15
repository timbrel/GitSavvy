import os
import re

import sublime
from sublime_plugin import WindowCommand

from GitSavvy.core.fns import filter_, maybe
from GitSavvy.core.runtime import enqueue_on_worker
from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.input_panel import show_single_line_input_panel


__all__ = (
    "gs_offer_init",
    "gs_init",
    "gs_clone",
    "gs_setup_user"
)


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
DEFAULT_PROJECT_ROOT = (
    os.path.expanduser(R'~\Desktop')
    if os.name == "nt"
    else os.path.expanduser('~')
)


views_with_offer_made = set()


class gs_offer_init(WindowCommand, GitCommand):

    """
    If a git command fails indicating no git repo was found, this
    command will ask the user whether they'd like to init a new repo.

    Offer only once per session for a given view.
    """

    def run(self):
        if self.savvy_settings.get("disable_git_init_prompt"):
            return

        view = self.window.active_view()
        if not view:
            return

        vid = view.id()
        if vid not in views_with_offer_made and sublime.ok_cancel_dialog(NO_REPO_MESSAGE):
            self.window.run_command("gs_init")
        else:
            views_with_offer_made.add(vid)


class gs_init(WindowCommand, GitCommand):

    """
    If the active Sublime window has folders added to the project (or if
    Sublime was opened from the terminal with something like `subl .`),
    initialize a new Git repo at that location.  If that directory cannot be
    determined, use the open file's directory.  If there is no open file,
    prompt the user for the directory to use.

    If the selected directory has previously been initialized with Git, prompt
    the user to confirm a re-initialize before proceeding.
    """

    def run(self):
        git_root = (
            maybe(lambda: self.window.folders()[0])
            or maybe(lambda: os.path.dirname(self._current_filename()))  # type: ignore[type-var]
        )
        if git_root and os.path.exists(os.path.join(git_root, ".git")):
            if sublime.ok_cancel_dialog(CONFIRM_REINITIALIZE):
                self.on_done(git_root, re_init=True)
            return

        show_single_line_input_panel(
            REPO_PATH_PROMPT,
            git_root or "",
            self.on_done,
            select_text=False
        )

    def on_done(self, path, re_init=False):
        if not path:
            return
        self.git("init", working_dir=path)
        self.window.status_message(
            "{word_start}nitialized repo successfully.".format(
                word_start="Re-i" if re_init else "I"
            )
        )
        util.view.refresh_gitsavvy(self.window.active_view())


HUBS = [
    "https://github.com/",
    "https://bitbucket.org/"
]


def parse_url_from_clipboard(clip_content):
    # type: (str) -> str
    if not clip_content:
        return ""

    if (
        clip_content.endswith(".git")
        and re.match(r"^(https?|git)://|git@", clip_content)
    ):
        return clip_content

    for hub in HUBS:
        if clip_content.startswith(hub):
            path = clip_content[len(hub):]
            try:
                owner, name = filter_(path.split("/")[:2])
            except ValueError:
                return ""
            else:
                return "{}{}/{}.git".format(hub, owner, name)
    return ""


def project_name_from_url(input_url):
    # URLs can come in various formats, for example
    # https://github.com/timbrel/GitSavvy.git
    #     git@github.com:divmain/GitSavvy.git
    _split_url = input_url.rsplit("/", 1)[-1]
    if _split_url.endswith(".git"):
        _split_url = _split_url[:-4]
    return _split_url


class gs_clone(WindowCommand, GitCommand):

    def run(self, recursive=False):
        self.recursive = recursive
        clip_content = sublime.get_clipboard(256).strip()
        show_single_line_input_panel(
            GIT_URL,
            parse_url_from_clipboard(clip_content),
            self.on_enter_url
        )

    def on_enter_url(self, url):
        if not url:
            return
        self.git_url = url
        suggested_git_root = self.find_suggested_git_root(url)
        show_single_line_input_panel(
            REPO_PATH_PROMPT,
            suggested_git_root,
            self.on_enter_directory,
            select_text=False
        )

    def find_suggested_git_root(self, url):
        # type: (str) -> str
        base_folder = self.guess_base_folder()
        project_name = project_name_from_url(url)
        return os.path.join(base_folder, project_name)

    def guess_base_folder(self):
        # type: () -> str
        return (
            maybe(lambda: os.path.dirname(self.window.folders()[0]))
            or self.default_project_root()
        )

    def on_enter_directory(self, path):
        if not path:
            return
        self.target_dir = os.path.expanduser(path)
        if os.path.exists(os.path.join(self.target_dir, ".git")):
            sublime.error_message(RECLONE_CANT_BE_DONE)
            return

        enqueue_on_worker(self.do_clone)

    def do_clone(self):
        if not self.window.folders() and not self.window.views():
            window = self.window
        else:
            window = open_new_window()
            # HACK: `git()` uses `self.window`, we can't pass anything down
            self.window = window

        window.status_message("Start cloning {}".format(self.git_url))
        self.git(
            "clone",
            "--progress",
            "--recursive" if self.recursive else None,
            self.git_url,
            self.target_dir,
            working_dir='.',
            show_panel=True
        )
        # Set this late to ensure `target_dir` actually exists
        window.set_project_data({
            "folders": [dict(follow_symlinks=True, path=self.target_dir)]
        })
        window.status_message("Cloned repo successfully.")
        if not window.is_sidebar_visible():
            self.window.run_command("toggle_side_bar")
        util.view.refresh_gitsavvy_interfaces(window, refresh_sidebar=True)


def open_new_window():
    # type: () -> sublime.Window
    sublime.run_command("new_window")
    return sublime.active_window()


class gs_setup_user(WindowCommand, GitCommand):

    """
    Set user's name and email address in global Git config.
    """

    def run(self):
        if sublime.ok_cancel_dialog(NO_CONFIG_MESSAGE):
            self.get_name()

    def get_name(self):
        show_single_line_input_panel(NAME_MESSAGE, "", self.on_done_name)

    def on_done_name(self, name):
        self.git("config", "--global", "user.name", "{}".format(name))
        self.get_email()

    def get_email(self):
        show_single_line_input_panel(EMAIL_MESSAGE, "", self.on_done_email)

    def on_done_email(self, email):
        self.git("config", "--global", "user.email", "{}".format(email))
