import sublime
from ..common import util


from typing import Optional, Sequence


class GitSavvyError(Exception):
    def __init__(self, msg, *, cmd=None, stdout="", stderr="", show_panel=True, window=None):
        # type: (str, Sequence[str], str, str, bool, Optional[sublime.Window]) -> None
        super(GitSavvyError, self).__init__(msg)
        self.message = msg
        self.cmd = cmd
        self.stdout = stdout
        self.stderr = stderr
        self.show_panel = show_panel
        self.window = window
        if msg:
            if show_panel:
                self.show_error_panel()
            util.debug.log_error(msg)

    def show_error_panel(self):
        util.log.display_panel(self.window or sublime.active_window(), self.message)


class FailedGithubRequest(GitSavvyError):
    pass


class FailedGitLabRequest(GitSavvyError):
    pass


class DetachedView(RuntimeError):
    pass
