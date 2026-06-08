from __future__ import annotations

import sublime_plugin

from GitSavvy.common import util
from GitSavvy.core.utils import abort_proc, flash


__all__ = (
    "gs_abort_output_panel",
)


class gs_abort_output_panel(sublime_plugin.TextCommand):
    def run(self, edit) -> None:
        proc = util.log.running_process_for_panel(self.view)
        if not proc:
            flash(self.view, "No process is currently running")
            return

        abort_proc(proc)
        flash(self.view, "Aborting git command…")
