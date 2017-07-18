import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from .log import LogMixin
from ..ui_mixins.quick_panel import show_paginated_panel


class RefLogMixin(object):

    _limit = 6000

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        show_paginated_panel(
            self.reflog_generator(limit=self._limit), self.do_action, limit=self._limit)

    def do_action(self, commit_hash):
        if hasattr(self, 'window'):
            window = self.window
        else:
            window = self.view.window()
        window.run_command("gs_log_action", {
            "commit_hash": commit_hash
        })


class GsRefLogCommand(RefLogMixin, WindowCommand, GitCommand):
    pass
