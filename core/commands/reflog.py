import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ..ui_mixins.quick_panel import show_paginated_panel


class RefLogMixin(object):

    _limit = 6000

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        show_paginated_panel(
            self.reflog_generator(limit=self._limit), self.on_done, limit=self._limit)

    def on_done(self, commit):
        if commit:
            self.do_action(commit)

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
