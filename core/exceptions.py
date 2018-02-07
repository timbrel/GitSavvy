import sublime
from ..common import util


class GitSavvyError(Exception):
    def __init__(self, msg, *args, **kwargs):
        super(GitSavvyError, self).__init__(msg, *args)
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
