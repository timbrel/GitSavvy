"""
Define a base command class that:
  1) provides a consistent interface with `git`,
  2) implements common git operations in one place, and
  3) tracks file- and repo- specific data the is necessary
     for Git operations.
"""

from collections import deque, ChainMap
import io
from itertools import chain, repeat
from functools import partial
import locale
import os
import subprocess
import shutil
import re
import time
import traceback

import sublime

from ..common import util
from .settings import SettingsMixin
from GitSavvy.core.fns import filter_
from GitSavvy.core.runtime import run_as_future


MYPY = False
if MYPY:
    from typing import Callable, Deque, Iterator, List, Sequence, Tuple, Union


git_path = None
error_message_displayed = False

DECODE_ERROR_MESSAGE = """
The Git command returned data that is unparsable.  This may happen
if you have checked binary data into your repository, or not UTF-8
encoded files.  In the latter case use the 'fallback_encoding' setting.

-- Partially decoded output follows; � denotes decoding errors --
"""

MIN_GIT_VERSION = (2, 16, 0)
GIT_TOO_OLD_MSG = "Your Git version is too old. GitSavvy requires {:d}.{:d}.{:d} or above."


def communicate_and_log(proc, stdin, log):
    # type: (subprocess.Popen, bytes, Callable[[bytes], None]) -> Tuple[bytes, bytes]
    """
    Emulates Popen.communicate
    Writes stdin (if provided)
    Logs output from both stdout and stderr
    Returns stdout, stderr
    """
    if stdin is not None:
        assert proc.stdin
        proc.stdin.write(stdin)
        proc.stdin.flush()
        proc.stdin.close()

    stdout, stderr = b'', b''
    for line in stream_stdout_and_err(proc):
        if isinstance(line, Out):
            stdout += line
            log(line)
        elif isinstance(line, Err):
            stderr += line
            log(line)

    return stdout, stderr


class Out(bytes): pass  # noqa: E701
class Err(bytes): pass  # noqa: E701


def read_linewise(fh, kont):
    # type: (io.BufferedReader, Callable[[bytes], None]) -> None
    for line in iter(fh.readline, b''):
        kont(line)


def stream_stdout_and_err(proc):
    # type: (subprocess.Popen) -> Iterator[bytes]
    container = deque()  # type: Deque[bytes]
    append = container.append
    out_f = run_as_future(read_linewise, proc.stdout, lambda line: append(Out(line)))
    err_f = run_as_future(read_linewise, proc.stderr, lambda line: append(Err(line)))
    delay = chain([1, 2, 4, 8, 15, 30], repeat(50))

    with proc:
        while out_f.running() or err_f.running():
            try:
                yield container.popleft()
            except IndexError:
                time.sleep(next(delay) / 1000)

    # Check and raise exceptions if any
    out_f.result()
    err_f.result()

    yield from container


STARTUPINFO = None
if os.name == "nt":
    STARTUPINFO = subprocess.STARTUPINFO()
    STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW


class _GitCommand(SettingsMixin):

    """
    Base class for all Sublime commands that interact with git.
    """

    def git(
        self,
        git_cmd,
        *args,
        stdin=None,
        working_dir=None,
        show_panel=None,
        show_panel_on_stderr=True,
        throw_on_stderr=True,
        decode=True,
        encode=True,
        stdin_encoding="UTF-8",
        custom_environ=None,
        just_the_proc=False
    ):
        """
        Run the git command specified in `*args` and return the output
        of the git command as a string.

        If stdin is provided, it should be a string and will be piped to
        the git process.  If `working_dir` is provided, set this as the
        current working directory for the git process; otherwise,
        the `repo_path` value will be used.
        """
        final_args = self._add_global_flags(git_cmd, list(args))
        command = [self.git_binary_path] + list(filter_(final_args))
        command_str = " ".join(["git"] + command[1:])

        if show_panel is None:
            show_panel = git_cmd in self.savvy_settings.get("show_panel_for")

        if show_panel:
            window = self.some_window()
            panel = util.log.init_panel(window)
            log = partial(util.log.append_to_panel, panel)
            log("$ {}\n".format(command_str))

        if not working_dir:
            try:
                working_dir = self.repo_path
            except RuntimeError as e:
                # do not show panel when the window does not exist
                raise GitSavvyError(str(e), show_panel=False)
            except Exception as e:
                raise GitSavvyError(str(e), show_panel=show_panel_on_stderr)

        stdout, stderr = None, None
        environ = ChainMap(
            custom_environ or {},
            self.savvy_settings.get("env") or {},
            os.environ
        )
        try:
            start = time.time()
            p = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=working_dir,
                env=environ,
                startupinfo=STARTUPINFO
            )

            if just_the_proc:
                return p

            if stdin is not None and encode:
                stdin = stdin.encode(encoding=stdin_encoding)

            if show_panel:
                log_b = lambda line: log(line.decode("utf-8", "replace"))
                stdout, stderr = communicate_and_log(p, stdin, log_b)
            else:
                stdout, stderr = p.communicate(stdin)

        except Exception as e:
            # this should never be reached
            raise GitSavvyError(
                "$ {} ({})\n\n"
                "Please report this error to GitSavvy:\n\n{}\n\n{}".format(
                    command_str, working_dir, e, traceback.format_exc()
                ),
                cmd=command,
                show_panel=show_panel_on_stderr)

        finally:
            if not just_the_proc:
                end = time.time()
                util.debug.log_git(final_args, stdin, stdout, stderr, end - start)
                if show_panel:
                    log("\n[Done in {:.2f}s]".format(end - start))

        if decode:
            try:
                stdout, stderr = self.strict_decode(stdout), self.strict_decode(stderr)  # type: ignore[assignment]
            except UnicodeDecodeError:
                stdout_s = stdout.decode("utf-8", "replace")
                stderr_s = stderr.decode("utf-8", "replace")
                raise GitSavvyError(
                    "$ {}\n{}{}{}".format(
                        command_str,
                        DECODE_ERROR_MESSAGE,
                        stdout_s,
                        stderr_s,
                    ),
                    cmd=command,
                    stdout=stdout_s,
                    stderr=stderr_s,
                    show_panel=show_panel_on_stderr
                )

        if throw_on_stderr and not p.returncode == 0:
            stdout_s, stderr_s = self.ensure_decoded(stdout), self.ensure_decoded(stderr)
            if "*** Please tell me who you are." in stderr_s:
                sublime.set_timeout_async(
                    lambda: sublime.active_window().run_command("gs_setup_user"))

            raise GitSavvyError(
                "$ {}\n\n{}{}".format(command_str, stdout_s, stderr_s),
                cmd=command,
                stdout=stdout_s,
                stderr=stderr_s,
                show_panel=show_panel_on_stderr
            )

        return stdout

    def git_throwing_silently(self, *args, **kwargs):
        return self.git(
            *args,
            throw_on_stderr=True,
            show_panel_on_stderr=False,
            **kwargs
        )

    def get_encoding_candidates(self):
        # type: () -> Sequence[str]
        return [
            'utf-8',
            locale.getpreferredencoding(),
            self.savvy_settings.get("fallback_encoding")
        ]

    def strict_decode(self, input):
        # type: (bytes) -> str
        encodings = self.get_encoding_candidates()
        decoded, _ = self.try_decode(input, encodings)
        return decoded

    def ensure_decoded(self, input):
        # type: (Union[str, bytes]) -> str
        if isinstance(input, str):
            return input
        return self.lax_decode(input)

    def lax_decode(self, input):
        # type: (bytes) -> str
        try:
            return self.strict_decode(input)
        except UnicodeDecodeError:
            return input.decode("utf-8", "replace")

    def try_decode(self, input, encodings):
        # type: (bytes, Sequence[str]) -> Tuple[str, str]
        for n, encoding in enumerate(encodings, start=1):
            try:
                return input.decode(encoding), encoding
            except UnicodeDecodeError as err:
                if n == len(encodings):
                    raise err
        assert False  # no silent fall-through

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
                stdout = subprocess.check_output(
                    [git_path, "--version"],
                    stderr=subprocess.PIPE,
                    startupinfo=STARTUPINFO
                ).decode()
            except Exception:
                stdout = ""
                git_path = None

            match = re.match(r"git version ([0-9]+)\.([0-9]+)\.([0-9]+)", stdout)
            if match:
                version = tuple(map(int, match.groups()))
                if version < MIN_GIT_VERSION:
                    msg = GIT_TOO_OLD_MSG.format(*MIN_GIT_VERSION)
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

    def get_repo_path(self, offer_init=True):
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
                        # offer initialization
                        if offer_init:
                            sublime.set_timeout_async(
                                lambda: sublime.active_window().run_command("gs_offer_init"))
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
    def repo_path(self):
        """
        Return the absolute path to the git repo that contains the file that this
        view interacts with.  Like `file_path`, this can be overridden by setting
        the view's `git_savvy.repo_path` setting.
        """
        return self.get_repo_path()

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

    def _add_global_flags(self, git_cmd, args):
        # type: (str, List[str]) -> List[str]
        """
        Transforms the Git command arguments with flags indicated in the
        global GitSavvy settings.
        """
        global_pre_flags = self.savvy_settings.get("global_pre_flags", {}).get(git_cmd, [])
        global_flags = self.savvy_settings.get("global_flags", {}).get(git_cmd, [])
        return global_pre_flags + [git_cmd] + global_flags + args


if MYPY:
    mixin_base = _GitCommand
else:
    mixin_base = object


from .git_mixins.status import StatusMixin  # noqa: E402
from .git_mixins.active_branch import ActiveBranchMixin  # noqa: E402
from .git_mixins.branches import BranchesMixin  # noqa: E402
from .git_mixins.stash import StashMixin  # noqa: E402
from .git_mixins.stage_unstage import StageUnstageMixin  # noqa: E402
from .git_mixins.checkout_discard import CheckoutDiscardMixin  # noqa: E402
from .git_mixins.remotes import RemotesMixin  # noqa: E402
from .git_mixins.ignore import IgnoreMixin  # noqa: E402
from .git_mixins.tags import TagsMixin  # noqa: E402
from .git_mixins.history import HistoryMixin  # noqa: E402
from .git_mixins.rewrite import RewriteMixin  # noqa: E402
from .git_mixins.merge import MergeMixin  # noqa: E402
from .exceptions import GitSavvyError  # noqa: E402


class GitCommand(
    StatusMixin,
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
    _GitCommand
):
    pass
