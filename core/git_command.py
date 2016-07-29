"""
Define a base command class that:
  1) provides a consistent interface with `git`,
  2) implements common git operations in one place, and
  3) tracks file- and repo- specific data the is necessary
     for Git operations.
"""

import os
import time
import subprocess
import shutil

import sublime
from ..common import util, vendor
from ..common.vendor import pexpect
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


git_path = None


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
                 TagsMixin,
                 HistoryMixin,
                 RewriteMixin,
                 MergeMixin
                 ):

    """
    Base class for all Sublime commands that interact with git.
    """

    _last_remotes_used = {}
    _quick_panel_blame_idx = 1
    _quick_panel_log_idx = 1
    _quick_panel_branch_diff_history_idx = 1
    _stdout = ''

    def git(self, *args,
            stdin=None,
            working_dir=None,
            show_panel=False,
            throw_on_stderr=True,
            decode=True,
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
        git_path_setting = sublime.load_settings("GitSavvy.sublime-settings")

        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        need_auth = ['push', 'pull', 'fetch']
        catch_credential_errors =  savvy_settings.get("prompt_credentials", False)
        catch_credential_errors = catch_credential_errors and args[0] in need_auth

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
            util.log.panel(msg)
            util.debug.log_error(msg)
            raise GitSavvyError(msg)

        try:
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            environ = os.environ.copy()
            environ.update(custom_environ or {})
            p = subprocess.Popen(command,
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 cwd=working_dir or self.repo_path,
                                 env=environ,
                                 startupinfo=startupinfo)
            stdout, stderr = p.communicate(stdin.encode(encoding="UTF-8") if stdin else None)
            if decode:
                stdout, stderr = self.decode_stdout(stdout, savvy_settings), stderr.decode()

        except Exception as e:
            raise_error(e)

        finally:
            if decode:
                util.debug.log_git(args, stdin, stdout, stderr)
            else:
                util.debug.log_git(
                    args,
                    stdin,
                    self.decode_stdout(stdout, savvy_settings),
                    stderr.decode()
                )

        def on_result(return_code, stdout, stderr):
            if decode and not isinstance(stdout, str):
                stdout, stderr = self.decode_stdout(stdout, savvy_settings), stderr.decode()

            if return_code != 0 and throw_on_stderr:
                raise_error("`{}` failed  ({}) with following output:\n{}\n{}".format(
                    command_str, return_code, stdout, stderr
                ))

            if show_panel:
                if savvy_settings.get("show_input_in_output"):
                    util.log.panel("> {}\n{}\n{}".format(command_str, stdout, stderr))
                else:
                    util.log.panel("{}\n{}".format(stdout, stderr))

        if not p.returncode == 0 and throw_on_stderr:
            # this piece of code detect git error meesages related to authentification.
            # stdin must be None, because we will use stdin to pass username and password from Sublime to Git.
            if catch_credential_errors and stdin is None and 'fatal: could not read Username for' in stderr:
                command = [self.git_binary_path, '-C', working_dir or self.repo_path] + list(arg for arg in args if arg)
                command_str = " ".join(command)

                self._stdout = None
                is_window_command = hasattr(self, "window")
                window = self.window if is_window_command else self.view.window()
                window.show_input_panel('username:', '', lambda username: self.on_username(window, command_str, username, on_result), None, self.on_cancel)

                # wait that the Git command has been executed in the show_input_panel thread.
                if is_window_command:
                    while self._stdout is None:
                        time.sleep(1)
                    stdout = self.decode_stdout(stdout, savvy_settings) if decode else self._stdout
                    return stdout

                # gwenzek: HACK: for TextCommand, it seems that the wait blocks the execution of `show_input_panel`.
                # The best solution I found is to return directly, but the output message that may be important isn't returned.
                # But it should work in most cases, since most commands that require the output doesn't require autenthification (log, status, ...)
                # Anyway, the output will still be shown to users if they ask for it in "show_panel_for" setting.
                else:
                    print('returning without waiting for stdout')
                    return None

        on_result(p.returncode, stdout, stderr)
        return stdout

    def on_username(self, window, command_str, username, on_result):
        """ Asks for the password, before calling the given command. """
        window.show_input_panel(username + "'s password:", '', lambda password: self.on_credentials(command_str, username, password, on_result), None, self.on_cancel)

    def on_credentials(self, command_str, username, password, on_result):
        """ Launch the given command, stores the ouput and return code in self. """
        # pexpect runs the given command in a subprocess, parses the output and feeds the username and password when asked by Git.
        # [pexpect](https://github.com/pexpect/pexpect)
        stdout, return_code = pexpect.run(command_str, events={'Username for': username + '\n', 'Password for': password + '\n'}, withexitstatus=True)
        on_result(return_code, stdout, b'')

    def on_cancel(self):
        self._stdout = b'Action cancelled by user.'
        self._returncode = 0

    def decode_stdout(self, stdout, savvy_settings):
        fallback_encoding = savvy_settings.get("fallback_encoding")
        silent_fallback = savvy_settings.get("silent_fallback")
        try:
            return stdout.decode()
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

        global git_path
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

        if not repo_path or not os.path.exists(repo_path):
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
        return os.path.relpath(os.path.realpath(path), start=os.path.realpath(self.repo_path))

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

    @property
    def quick_panel_blame_idx(self):
        """ Index for quick panel blame options"""
        return self._quick_panel_blame_idx

    @quick_panel_blame_idx.setter
    def quick_panel_blame_idx(self, value):
        self._quick_panel_blame_idx = value

    @property
    def quick_panel_log_idx(self):
        """ Index for quick panel blame options"""
        return self._quick_panel_log_idx

    @quick_panel_log_idx.setter
    def quick_panel_log_idx(self, value):
        self._quick_panel_log_idx = value

    @property
    def quick_panel_branch_diff_history_idx(self):
        """ Index for quick panel branch diff commit history options"""
        return self._quick_panel_branch_diff_history_idx

    @quick_panel_branch_diff_history_idx.setter
    def quick_panel_branch_diff_history_idx(self, value):
        self._quick_panel_branch_diff_history_idx = value
