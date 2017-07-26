"""
Define a base command class that:
  1) provides a consistent interface with `git`,
  2) implements common git operations in one place, and
  3) tracks file- and repo- specific data the is necessary
     for Git operations.
"""

import os
import subprocess
import shutil

import sublime

from ..common import util
from .git_mixins.status import StatusMixin
from .git_mixins.active_branch import ActiveBranchMixin
from .git_mixins.branches import BranchesMixin
from .git_mixins.stash import StashMixin
from .git_mixins.stage_unstage import StageUnstageMixin
from .git_mixins.checkout_discard import CheckoutDiscardMixin
from .git_mixins.remotes import RemotesMixin
from .git_mixins.ignore import IgnoreMixin
from .git_mixins.tags import TagsMixin
from .git_mixins.history import HistoryMixin
from .git_mixins.rewrite import RewriteMixin
from .git_mixins.merge import MergeMixin
from .exceptions import GitSavvyError
import time

git_path = None
error_message_displayed = False

UTF8_PARSE_ERROR_MSG = (
    "GitSavvy was unable to parse Git output as UTF-8. Would "
    "you like to use the fallback encoding specified in GitSavvy "
    "settings? Text may not appear as expected."
)

FALLBACK_PARSE_ERROR_MSG = (
    "The Git command returned data that unparsable.  This may happen "
    "if you have checked binary data into your repository.  The current "
    "operation has been aborted."
)


class GitCommand(StatusMixin,
                 ActiveBranchMixin,
                 BranchesMixin,
                 StashMixin,
                 StageUnstageMixin,
                 CheckoutDiscardMixin,
                 RemotesMixin,
                 IgnoreMixin,
                 TagsMixin,
                 HistoryMixin,
                 RewriteMixin,
                 MergeMixin
                 ):

    """
    Base class for all Sublime commands that interact with git.
    """

    _last_remotes_used = {}

    def git(self, *args,
            stdin=None,
            working_dir=None,
            show_panel=False,
            throw_on_stderr=True,
            decode=True,
            encode=True,
            stdin_encoding="UTF-8",
            custom_environ=None):
        """
        Run the git command specified in `*args` and return the output
        of the git command as a string.

        If stdin is provided, it should be a string and will be piped to
        the git process.  If `working_dir` is provided, set this as the
        current working directory for the git process; otherwise,
        the `repo_path` value will be used.
        """
        args = self._include_global_flags(args)
        command = (self.git_binary_path, ) + tuple(arg for arg in args if arg)
        command_str = " ".join(command)

        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")

        show_panel_overrides = savvy_settings.get("show_panel_for")
        show_panel = show_panel or args[0] in show_panel_overrides

        close_panel_for = savvy_settings.get("close_panel_for") or []
        if args[0] in close_panel_for:
            sublime.active_window().run_command("hide_panel", {"cancel": True})

        stdout, stderr = None, None

        def raise_error(msg):
            if type(msg) == str and "fatal: Not a git repository" in msg:
                sublime.set_timeout_async(
                    lambda: sublime.active_window().run_command("gs_offer_init"))

            elif type(msg) == str and "*** Please tell me who you are." in msg:
                sublime.set_timeout_async(
                    lambda: sublime.active_window().run_command("gs_setup_user"))

            sublime.status_message(
                "Failed to run `git {}`. See log for details.".format(command[1])
            )
            raise GitSavvyError(msg)

        try:
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            environ = os.environ.copy()
            environ.update(custom_environ or {})
            start = time.time()
            p = subprocess.Popen(command,
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 cwd=working_dir or self.repo_path,
                                 env=environ,
                                 startupinfo=startupinfo)
            stdout, stderr = p.communicate((stdin.encode(encoding=stdin_encoding) if encode else stdin) if stdin else None)
            if decode:
                stdout, stderr = self.decode_stdout(stdout, savvy_settings), stderr.decode()

        except Exception as e:
            raise_error(e)

        finally:
            end = time.time()
            if decode:
                util.debug.log_git(args, stdin, stdout, stderr, end - start)
            else:
                util.debug.log_git(
                    args,
                    stdin,
                    self.decode_stdout(stdout, savvy_settings),
                    stderr.decode(),
                    end - start
                )

        if not p.returncode == 0 and throw_on_stderr:
            raise_error("`{}` failed with following output:\n{}\n{}".format(
                command_str, stdout, stderr
            ))

        if show_panel:
            if savvy_settings.get("show_input_in_output"):
                util.log.panel("> {}\n{}\n{}".format(command_str, stdout, stderr))
            else:
                util.log.panel("{}\n{}".format(stdout, stderr))

        return stdout

    def decode_stdout(self, stdout, savvy_settings):
        fallback_encoding = savvy_settings.get("fallback_encoding")
        silent_fallback = savvy_settings.get("silent_fallback")
        try:
            return stdout.decode()
        except UnicodeDecodeError as unicode_err:
            try:
                return stdout.decode("latin-1")
            except UnicodeDecodeError as unicode_err:
                if silent_fallback or sublime.ok_cancel_dialog(UTF8_PARSE_ERROR_MSG, "Fallback?"):
                    try:
                        return stdout.decode(fallback_encoding)
                    except UnicodeDecodeError as fallback_err:
                        sublime.error_message(FALLBACK_PARSE_ERROR_MSG)
                        raise fallback_err
                raise unicode_err

    @property
    def encoding(self):
        return "UTF-8"

    @property
    def git_binary_path(self):
        """
        Return the path to the available `git` binary.
        """

        global git_path, error_message_displayed
        if not git_path:
            git_path_setting = sublime.load_settings("GitSavvy.sublime-settings").get("git_path")
            if isinstance(git_path_setting, dict):
                git_path = git_path_setting.get(sublime.platform())
                if not git_path:
                    git_path = git_path_setting.get('default')
            else:
                git_path = git_path_setting

            if not git_path:
                git_path = shutil.which("git")

        if not git_path:
            msg = ("Your Git binary cannot be found.  If it is installed, add it "
                   "to your PATH environment variable, or add a `git_path` setting "
                   "in the `User/GitSavvy.sublime-settings` file.")
            if not error_message_displayed:
                sublime.error_message(msg)
                error_message_displayed = True
            raise ValueError("Git binary not found.")

        return git_path

    def find_working_dir(self):
        view = self.window.active_view() if hasattr(self, "window") else self.view
        window = view.window()

        if not window:
            return None

        file_path = view.file_name()
        if file_path:
            file_dir = os.path.dirname(file_path)
            if os.path.isdir(file_dir):
                return os.path.dirname(file_path)

        open_folders = view.window().folders()
        if open_folders and os.path.isdir(open_folders[0]):
            return open_folders[0]

        return None

    def find_repo_path(self, throw_on_stderr=False):
        """
        Similar to find_working_dir, except that it does not stop on the first
        directory found, rather on the first git repository found.
        """
        view = self.window.active_view() if hasattr(self, "window") else self.view
        file_name = view.file_name()
        repo_path = None

        # try the current file first
        if file_name:
            file_dir = os.path.dirname(file_name)
            if os.path.isdir(file_dir):
                repo_path = self.find_git_toplevel(file_dir, throw_on_stderr=False)

        # fallback: use the first folder if the current file is not inside a git repo
        if not repo_path:
            window = sublime.active_window()
            if window:
                folders = window.folders()
                if folders and os.path.isdir(folders[0]):
                    # we don't set "git_savvy.repo_path" for a out-of-git-repo file
                    repo_path = self.find_git_toplevel(
                        folders[0], throw_on_stderr=throw_on_stderr)
        return repo_path

    def find_git_toplevel(self, folder, throw_on_stderr):
        stdout = self.git(
            "rev-parse",
            "--show-toplevel",
            working_dir=folder,
            throw_on_stderr=throw_on_stderr
            )
        repo = stdout.strip()
        return os.path.realpath(repo) if repo else None

    @property
    def repo_path(self):
        return self._repo_path()

    @property
    def short_repo_path(self):
        if "HOME" in os.environ:
            return self.repo_path.replace(os.environ["HOME"], "~")
        else:
            return self.repo_path

    def _repo_path(self, throw_on_stderr=True):
        """
        Return the absolute path to the git repo that contains the file that this
        view interacts with.  Like `file_path`, this can be overridden by setting
        the view's `git_savvy.repo_path` setting.

        Do not raise error when throw_on_stderr is `False`. It is needed
        in GsUpdateStatusBarCommand, otherwise, spurious popup will be shown.
        """
        # The below condition will be true if run from a WindowCommand and false
        # from a TextCommand.
        view = self.window.active_view() if hasattr(self, "window") else self.view
        repo_path = view.settings().get("git_savvy.repo_path") if view else None

        if not repo_path or not os.path.exists(repo_path):
            repo_path = self.find_repo_path(throw_on_stderr=throw_on_stderr)
            if not repo_path:
                if throw_on_stderr:
                    raise ValueError("Unable to determine Git repo path.")
                else:
                    return None
            else:
                view.settings().set("git_savvy.repo_path", os.path.realpath(repo_path))

        return os.path.realpath(repo_path) if repo_path else repo_path

    @property
    def file_path(self):
        """
        Return the absolute path to the file this view interacts with. In most
        cases, this will be the open file.  However, for views with special
        functionality, this default behavior can be overridden by setting the
        view's `git_savvy.file_path` setting.
        """
        # The below condition will be true if run from a WindowCommand and false
        # from a TextCommand.
        view = self.window.active_view() if hasattr(self, "window") else self.view
        fpath = view.settings().get("git_savvy.file_path")

        if not fpath:
            fpath = view.file_name()
            if fpath:
                view.settings().set("git_savvy.file_path", os.path.realpath(fpath))

        return os.path.realpath(fpath) if fpath else fpath

    def get_rel_path(self, abs_path=None):
        """
        Return the file path relative to the repo root.
        """
        path = abs_path or self.file_path
        return os.path.relpath(os.path.realpath(path), start=self.repo_path)

    def _include_global_flags(self, args):
        """
        Transforms the Git command arguments with flags indicated in the
        global GitSavvy settings.
        """
        git_cmd, *addl_args = args

        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        global_flags = savvy_settings.get("global_flags")

        if global_flags and git_cmd in global_flags:
            args = [git_cmd] + global_flags[git_cmd] + addl_args

        return args

    @property
    def last_remote_used(self):
        """
        With this getter and setter, keep global track of last remote used
        for each repo.  Will return whatever was set last, or active remote
        if never set. If there is no tracking remote, use "origin".
        """
        remote = self._last_remotes_used.get(self.repo_path)
        if not remote:
            remote = self.get_upstream_for_active_branch().split("/")[0]
        if not remote:
            remote = "origin"
        return remote

    @last_remote_used.setter
    def last_remote_used(self, value):
        """
        Setter for above property.  Saves per-repo information in
        class attribute dict.
        """
        self._last_remotes_used[self.repo_path] = value
