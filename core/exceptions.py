import sublime
from ..common import util


MYPY = False
if MYPY:
    from typing import Sequence


class GitSavvyError(Exception):
    def __init__(self, msg, *args, cmd=None, stdout="", stderr="", show_panel=True, **kwargs):
        # type: (str, object, Sequence[str], str, str, bool, object) -> None
        super(GitSavvyError, self).__init__(msg, *args)
        self.message = msg
        self.cmd = cmd
        self.stdout = stdout
        self.stderr = stderr
        if msg:
            if show_panel:
                util.log.display_panel(sublime.active_window(), msg)
            util.debug.log_error(msg)


class FailedGithubRequest(GitSavvyError):
    pass


class FailedGitLabRequest(GitSavvyError):
    pass
