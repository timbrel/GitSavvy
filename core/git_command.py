"""
Define a base command class that:
  1) provides a consistent interface with `git`,
  2) implements common git operations in one place, and
  3) tracks file- and repo- specific data the is necessary
     for Git operations.
"""

import os
import subprocess
import sublime

from ..common import util
from .file_and_repo import FileAndRepo
from .git_mixins.status import StatusMixin
from .git_mixins.active_branch import ActiveBranchMixin
from .git_mixins.branches import BranchesMixin
from .git_mixins.stash import StashMixin
from .git_mixins.stage_unstage import StageUnstageMixin
from .git_mixins.checkout_discard import CheckoutDiscardMixin
from .git_mixins.remotes import RemotesMixin
from .git_mixins.ignore import IgnoreMixin


class GitSavvyError(Exception):
    pass


class GitCommand(FileAndRepo,
                 StatusMixin,
                 ActiveBranchMixin,
                 BranchesMixin,
                 StashMixin,
                 StageUnstageMixin,
                 CheckoutDiscardMixin,
                 RemotesMixin,
                 IgnoreMixin
                 ):

    """
    Base class for all Sublime commands that interact with git.
    """

    def git(self, *args, stdin=None, working_dir=None, show_panel=False):
        """
        Run the git command specified in `*args` and return the output
        of the git command as a string.

        If stdin is provided, it should be a string and will be piped to
        the git process.  If `working_dir` is provided, set this as the
        current working directory for the git process; otherwise,
        the `repo_path` value will be used.
        """
        command = (self.git_binary_path, ) + tuple(arg for arg in args if arg)
        command_str = " ".join(command)

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

        if not p.returncode == 0:
            raise_error("`{}` failed with following output:\n{}\n{}".format(
                command_str, stdout, stderr
            ))

        if show_panel:
            util.log.panel("> {}\n{}\n{}".format(command_str, stdout, stderr))

        return stdout

    def get_read_only_view(self, name):
        """
        Create and return a read-only view.
        """
        window = self.window if hasattr(self, "window") else self.view.window()
        view = window.new_file()
        view.settings().set("git_savvy.{}_view".format(name), True)
        view.set_scratch(True)
        view.set_read_only(True)
        return view
