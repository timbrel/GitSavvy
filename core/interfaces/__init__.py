import sublime
from sublime_plugin import TextCommand

from .status import *  # noqa: F401, F403
from .branch import *  # noqa: F401, F403
from .rebase import *  # noqa: F401, F403
from .tags import *  # noqa: F401, F403
from ..git_command import GitCommand


class GsTabCycleCommand(TextCommand, GitCommand):
    commands = {
        "status": "gs_show_status",
        "branch": "gs_show_branch",
        "rebase": "gs_show_rebase",
        "tags": "gs_show_tags",
        "graph": "gs_log_graph_current_branch"
    }

    view_signatures = {
        "status": "git_savvy.status_view",
        "branch": "git_savvy.branch_view",
        "rebase": "git_savvy.rebase_view",
        "tags": "git_savvy.tags_view",
        "graph": "git_savvy.log_graph_view"
    }

    def run(self, edit, source=None, target=None, reverse=False):
        sublime.set_timeout_async(lambda: self.run_async(source, target, reverse))

    def run_async(self, source, target, reverse):
        to_load = target or self.get_next(source, reverse)
        if not to_load:
            return
        view_signature = self.view_signatures[to_load]

        window = self.view.window()
        if window:
            window.run_command(self.commands[to_load])
            if not self.view.settings().get(view_signature):
                sublime.set_timeout_async(self.view.close)

    def get_next(self, source, reverse=False):
        tab_order = self.savvy_settings.get("tab_order")

        if reverse is True:
            tab_order.reverse()

        if source not in tab_order:
            return None

        source_idx = tab_order.index(source)
        next_idx = 0 if source_idx == len(tab_order) - 1 else source_idx + 1

        return tab_order[next_idx]
