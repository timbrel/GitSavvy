import sublime

from ..ui_mixins.quick_panel import show_paginated_panel
from GitSavvy.core.base_commands import GsWindowCommand


__all__ = (
    "gs_ref_log",
)


class RefLogMixin(GsWindowCommand):

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
        self.window.run_command("gs_log_action", {
            "commit_hash": commit_hash
        })


class gs_ref_log(RefLogMixin):
    pass
