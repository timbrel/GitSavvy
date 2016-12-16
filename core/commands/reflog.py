import sublime
from ..ui_mixins.quick_panel import show_paginated_panel
from ...common import util


class RefLogMixin(object):

    _limit = 6000

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        show_paginated_panel(self.log_generator(), self.do_action, limit=self._limit)

    def log_generator(self):
        skip = 0
        while True:
            logs = self.reflog(limit=self._limit, skip=skip)
            if not logs:
                break
            for l in logs:
                yield (["{} {}".format(l.reflog_selector, l.reflog_name),
                        "{} {}".format(l.short_hash, l.summary),
                        "{}, {}".format(l.author, util.dates.fuzzy(l.datetime))],
                       l.long_hash)
            skip = skip + self._limit

    def do_action(self, commit_hash):
        if hasattr(self, 'window'):
            window = self.window
        else:
            window = self.view.window()
        window.run_command("gs_log_action", {
            "commit_hash": commit_hash,
            "file_path": self._file_path
        })
