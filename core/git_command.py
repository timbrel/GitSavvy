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
import re
import threading
import traceback

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
from .settings import SettingsMixin
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

GIT_TOO_OLD_MSG = "Your Git version is too old. GitSavvy requires {:d}.{:d}.{:d} or above."

# git minimum requirement
GIT_REQUIRE_MAJOR = 1
GIT_REQUIRE_MINOR = 9
GIT_REQUIRE_PATCH = 0


class LoggingProcessWrapper(object):

    """
    Wraps a Popen object with support for logging stdin/stderr
    """
    def __init__(self, process, timeout):
        self.timeout = timeout
        self.process = process
        self.stdout = b''
        self.stderr = b''

    def read_stdout(self):
        try:
            for line in iter(self.process.stdout.readline, b""):
                self.stdout = self.stdout + line
                util.log.panel_append(line.decode())
        except IOError as err:
            util.log.panel_append(err)

    def read_stderr(self):
        try:
            for line in iter(self.process.stderr.readline, b""):
                self.stderr = self.stderr + line
                util.log.panel_append(line.decode())
        except IOError as err:
            util.log.panel_append(err)

    def communicate(self, stdin):
        """
        Emulates Popen.communicate
        Writes stdin (if provided)
        Logs output from both stdout and stderr
        Returns stdout, stderr
        """
        if stdin is not None:
            self.process.stdin.write(stdin)
            self.process.stdin.flush()
            self.process.stdin.close()

        stdout_thread = threading.Thread(target=self.read_stdout)
        stdout_thread.start()
        stderr_thread = threading.Thread(target=self.read_stderr)
        stderr_thread.start()

        self.process.wait()

        stdout_thread.join(self.timeout / 1000)
        stderr_thread.join(self.timeout / 1000)

        return self.stdout, self.stderr


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
                 MergeMixin,
                 SettingsMixin
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

        show_panel_overrides = self.savvy_settings.get("show_panel_for")
        show_panel = show_panel or args[0] in show_panel_overrides

        close_panel_for = self.savvy_settings.get("close_panel_for") or []
        if args[0] in close_panel_for:
            sublime.active_window().run_command("hide_panel", {"cancel": True})

        live_panel_output = self.savvy_settings.get("live_panel_output", False)

        stdout, stderr = None, None

        try:
            if not working_dir:
                working_dir = self.repo_path
        except RuntimeError as e:
            # do not show panel when the window does not exist
            raise GitSavvyError(e, show_panel=False)
        except Exception as e:
            # offer initialization when "Not a git repository" is thrown from self.repo_path
            if type(e) == ValueError and e.args and "Not a git repository" in e.args[0]:
                sublime.set_timeout_async(
                    lambda: sublime.active_window().run_command("gs_offer_init"))
            raise GitSavvyError(e)

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
                                 cwd=working_dir,
                                 env=environ,
                                 startupinfo=startupinfo)

            def initialize_panel():
                # clear panel
                util.log.panel("")
                if self.savvy_settings.get("show_stdin_in_output") and stdin is not None:
                    util.log.panel_append("STDIN\n{}\n".format(stdin))
                if self.savvy_settings.get("show_input_in_output"):
                    util.log.panel_append("> {}\n".format(command_str))

            if show_panel and live_panel_output:
                wrapper = LoggingProcessWrapper(p, self.savvy_settings.get("live_panel_output_timeout", 10000))
                initialize_panel()

            if stdin is not None and encode:
                stdin = stdin.encode(encoding=stdin_encoding)

            if show_panel and live_panel_output:
                stdout, stderr = wrapper.communicate(stdin)
            else:
                stdout, stderr = p.communicate(stdin)

            if decode:
                stdout, stderr = self.decode_stdout(stdout), self.decode_stdout(stderr)

            if show_panel and not live_panel_output:
                initialize_panel()
                if stdout:
                    util.log.panel_append(stdout)
                if stderr:
                    if stdout:
                        util.log.panel_append("\n")
                    util.log.panel_append(stderr)

        except Exception as e:
            # this should never be reached
            raise GitSavvyError("Please report this error to GitSavvy:\n\n{}\n\n{}".format(e, traceback.format_exc()))

        finally:
            end = time.time()
            if decode:
                util.debug.log_git(args, stdin, stdout, stderr, end - start)
            else:
                util.debug.log_git(
                    args,
                    stdin,
                    self.decode_stdout(stdout),
                    self.decode_stdout(stderr),
                    end - start
                )

            if show_panel and self.savvy_settings.get("show_time_elapsed_in_output", True):
                util.log.panel_append("\n[Done in {:.2f}s]".format(end - start))

        if throw_on_stderr and not p.returncode == 0:
            sublime.active_window().status_message(
                "Failed to run `git {}`. See log for details.".format(command[1])
            )

            if "*** Please tell me who you are." in stderr:
                sublime.set_timeout_async(
                    lambda: sublime.active_window().run_command("gs_setup_user"))

            if stdout or stderr:
                raise GitSavvyError("`{}` failed with following output:\n{}\n{}".format(
                    command_str, stdout, stderr
                ))
            else:
                raise GitSavvyError("`{}` failed.".format(command_str))

        return stdout

    def decode_stdout(self, stdout):
        fallback_encoding = self.savvy_settings.get("fallback_encoding")
        silent_fallback = self.savvy_settings.get("silent_fallback")
        try:
            return stdout.decode()
        except UnicodeDecodeError:
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
            git_path_setting = self.savvy_settings.get("git_path")
            if isinstance(git_path_setting, dict):
                git_path = git_path_setting.get(sublime.platform())
                if not git_path:
                    git_path = git_path_setting.get('default')
            else:
                git_path = git_path_setting

            if not git_path:
                git_path = shutil.which("git")

            try:
                startupinfo = None
                if os.name == "nt":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

                stdout = subprocess.check_output(
                    [git_path, "--version"],
                    stderr=subprocess.PIPE,
                    startupinfo=startupinfo).decode("utf-8")

            except Exception:
                stdout = ""
                git_path = None

            match = re.match(r"git version ([0-9]+)\.([0-9]+)\.([0-9]+)", stdout)
            if match:
                major = int(match.group(1))
                minor = int(match.group(2))
                patch = int(match.group(3))
                if major < GIT_REQUIRE_MAJOR \
                        or (major == GIT_REQUIRE_MAJOR and minor < GIT_REQUIRE_MINOR) \
                        or (major == GIT_REQUIRE_MAJOR and minor == GIT_REQUIRE_MINOR and patch < GIT_REQUIRE_PATCH):
                    msg = GIT_TOO_OLD_MSG.format(
                        GIT_REQUIRE_MAJOR,
                        GIT_REQUIRE_MINOR,
                        GIT_REQUIRE_PATCH)
                    git_path = None
                    if not error_message_displayed:
                        sublime.error_message(msg)
                        error_message_displayed = True
                    raise ValueError("Git binary too old.")

        if not git_path:
            msg = ("Your Git binary cannot be found.  If it is installed, add it "
                   "to your PATH environment variable, or add a `git_path` setting "
                   "in the GitSavvy settings.")
            if not error_message_displayed:
                sublime.error_message(msg)
                error_message_displayed = True
            raise ValueError("Git binary not found.")

        return git_path

    def find_working_dir(self):
        view = self.window.active_view() if hasattr(self, "window") else self.view
        window = view.window() if view else None

        if view and view.file_name():
            file_dir = os.path.dirname(view.file_name())
            if os.path.isdir(file_dir):
                return file_dir

        if window:
            folders = window.folders()
            if folders and os.path.isdir(folders[0]):
                return folders[0]

        return None

    def find_repo_path(self):
        """
        Similar to find_working_dir, except that it does not stop on the first
        directory found, rather on the first git repository found.
        """
        view = self.window.active_view() if hasattr(self, "window") else self.view
        window = view.window() if view else None
        repo_path = None

        # try the current file first
        if view and view.file_name():
            file_dir = os.path.dirname(view.file_name())
            if os.path.isdir(file_dir):
                repo_path = self.find_git_toplevel(file_dir, throw_on_stderr=False)

        # fallback: use the first folder if the current file is not inside a git repo
        if not repo_path:
            if window:
                folders = window.folders()
                if folders and os.path.isdir(folders[0]):
                    repo_path = self.find_git_toplevel(
                        folders[0], throw_on_stderr=False)

        return os.path.realpath(repo_path) if repo_path else None

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
        """
        Return the absolute path to the git repo that contains the file that this
        view interacts with.  Like `file_path`, this can be overridden by setting
        the view's `git_savvy.repo_path` setting.
        """
        # The below condition will be true if run from a WindowCommand and false
        # from a TextCommand.
        view = self.window.active_view() if hasattr(self, "window") else self.view
        repo_path = view.settings().get("git_savvy.repo_path") if view else None

        if not repo_path or not os.path.exists(repo_path):
            repo_path = self.find_repo_path()
            if not repo_path:
                window = view.window()
                if window:
                    if window.folders():
                        raise ValueError("Not a git repository.")
                    else:
                        raise ValueError("Unable to determine Git repo path.")
                else:
                    raise RuntimeError("Window does not exist.")

            if view:
                file_name = view.file_name()
                # only set "git_savvy.repo_path" when the current file is in repo_path
                if file_name and os.path.realpath(file_name).startswith(repo_path + os.path.sep):
                    view.settings().set("git_savvy.repo_path", repo_path)

        return os.path.realpath(repo_path) if repo_path else repo_path

    @property
    def short_repo_path(self):
        if "HOME" in os.environ:
            return self.repo_path.replace(os.environ["HOME"], "~")
        else:
            return self.repo_path

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

        global_flags = self.savvy_settings.get("global_flags")

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
