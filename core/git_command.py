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


git_path = None


class GitSavvyError(Exception):
    pass


class GitCommand(StatusMixin,
                 ActiveBranchMixin,
                 BranchesMixin,
                 StashMixin,
                 StageUnstageMixin,
                 CheckoutDiscardMixin,
                 RemotesMixin,
                 IgnoreMixin,
                 TagsMixin
                 ):

    """
    Base class for all Sublime commands that interact with git.
    """

    _last_remotes_used = {}

    def git(self, *args, stdin=None, working_dir=None, show_panel=False, throw_on_stderr=True):
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

        gitsavvy_settings = sublime.load_settings("GitSavvy.sublime-settings")

        show_panel_overrides = gitsavvy_settings.get("show_panel_for")
        show_panel = show_panel or args[0] in show_panel_overrides

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
            util.log.panel(msg)
            util.debug.log_error(msg)
            raise GitSavvyError(msg)

        try:
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            p = subprocess.Popen(command,
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 cwd=working_dir or self.repo_path,
                                 env=os.environ,
                                 startupinfo=startupinfo)
            stdout, stderr = p.communicate(stdin.encode(encoding="UTF-8") if stdin else None)
            stdout, stderr = stdout.decode(), stderr.decode()

        except Exception as e:
            raise_error(e)

        finally:
            util.debug.log_git(args, stdin, stdout, stderr)

        if not p.returncode == 0 and throw_on_stderr:
            raise_error("`{}` failed with following output:\n{}\n{}".format(
                command_str, stdout, stderr
            ))

        if show_panel:
            if gitsavvy_settings.get("show_input_in_output"):
                util.log.panel("> {}\n{}\n{}".format(command_str, stdout, stderr))
            else:
                util.log.panel("{}\n{}".format(stdout, stderr))

        return stdout

    @property
    def encoding(self):
        return "UTF-8"

    @property
    def git_binary_path(self):
        """
        Return the path to the available `git` binary.
        """

        global git_path
        git_path = (git_path or
                    sublime.load_settings("GitSavvy.sublime-settings").get("gitPath") or
                    shutil.which("git")
                    )

        if not git_path:
            msg = ("Your Git binary cannot be found.  If it is installed, add it "
                   "to your PATH environment variable, or add a `gitPath` setting "
                   "in the `User/GitSavvy.sublime-settings` file.")
            sublime.error_message(msg)
            raise ValueError("Git binary not found.")

        return git_path

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
        """
        def invalid_repo():
            if throw_on_stderr:
                raise ValueError("Unable to determine Git repo path.")
            return None

        # The below condition will be true if run from a WindowCommand and false
        # from a TextCommand.
        view = self.window.active_view() if hasattr(self, "window") else self.view
        repo_path = view.settings().get("git_savvy.repo_path")

        if not repo_path:
            file_path = self.file_path
            file_dir = os.path.dirname(file_path) if file_path else None
            working_dir = file_path and os.path.isdir(file_dir) and file_dir

            if not working_dir:
                window_folders = sublime.active_window().folders()
                if not window_folders or not os.path.isdir(window_folders[0]):
                    return invalid_repo()
                working_dir = window_folders[0]

            stdout = self.git(
                "rev-parse",
                "--show-toplevel",
                working_dir=working_dir,
                throw_on_stderr=throw_on_stderr
                )

            repo_path = stdout.strip()

            if not repo_path:
                return invalid_repo()

            view.settings().set("git_savvy.repo_path", repo_path)

        return repo_path

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
            view.settings().set("git_savvy.file_path", fpath)

        return fpath

    def get_rel_path(self, abs_path=None):
        """
        Return the file path relative to the repo root.
        """
        path = abs_path or self.file_path
        return os.path.relpath(path, start=self.repo_path)

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
        for each repo.  Will return whatever was set last, or "origin" if
        never set.
        """
        return self._last_remotes_used.get(self.repo_path, "origin")

    @last_remote_used.setter
    def last_remote_used(self, value):
        """
        Setter for above property.  Saves per-repo information in
        class attribute dict.
        """
        self._last_remotes_used[self.repo_path] = value
