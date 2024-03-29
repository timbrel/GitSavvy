import sublime
from sublime_plugin import TextCommand

from .status import *  # noqa: F401, F403
from .branch import *  # noqa: F401, F403
from .rebase import *  # noqa: F401, F403
from .tags import *  # noqa: F401, F403
from ..git_command import GitCommand


class gs_tab_cycle(TextCommand, GitCommand):
    commands = {
        "status": "gs_show_status",
        "branch": "gs_show_branch",
        "rebase": "gs_show_rebase",
        "tags": "gs_show_tags",
        "graph": "gs_log_graph_current_branch"
    }

    def run(self, edit, source=None, target=None, reverse=False):
        to_load = target or self.get_next(source, reverse)
        if not to_load:
            return

        window = self.view.window()
        if window:
            window.run_command(self.commands[to_load])
            if not self.view.settings().get("git_savvy.log_graph_view"):
                self.view.close()

    def get_next(self, source, reverse=False):
        tab_order = self.savvy_settings.get("tab_order")

        if reverse:
            tab_order = list(reversed(tab_order))

        if source not in tab_order:
            return None

        source_idx = tab_order.index(source)
        next_idx = 0 if source_idx == len(tab_order) - 1 else source_idx + 1

        return tab_order[next_idx]
