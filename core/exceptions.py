import sublime
from ..common import util


class GitSavvyError(Exception):
    def __init__(self, msg, *args, cmd=None, stdout=None, stderr=None, **kwargs):
        super(GitSavvyError, self).__init__(msg, *args)
        self.message = msg
        self.cmd = cmd
        self.stdout = stdout
        self.stderr = stderr
        if msg:
            if kwargs.get('show_panel', True):
                util.log.panel(msg)
            if kwargs.get('show_status', False):
                sublime.active_window().status_message(msg)
            util.debug.log_error(msg)


class FailedGithubRequest(GitSavvyError):
    pass


class FailedGitLabRequest(GitSavvyError):
    pass
