import sublime
from sublime_plugin import TextCommand

from .status import *
from .branch import *
from .rebase import *
from .tags import *


class GsTabCycleCommand(TextCommand, GitCommand):
    commands = {
        "status": "gs_show_status",
        "branch": "gs_show_branch",
        "rebase": "gs_show_rebase",
        "tags": "gs_show_tags",
        "graph": "gs_log_graph"
    }

    view_signatures = {
        "status": "git_savvy.status_view",
        "branch": "git_savvy.branch_view",
        "rebase": "git_savvy.rebase_view",
        "tags": "git_savvy.tags_view",
        "graph": "git_savvy.log_graph_view"
    }

    def run(self, edit, source=None, target=None, reverse=False):
        to_load = target or self.get_next(source, reverse)
        view_signature = self.view_signatures[to_load]

        self.view.window().run_command("hide_panel", {"cancel": True})

        self.view.window().run_command(self.commands[to_load])
        if not self.view.settings().get(view_signature):
            self.view.close()

    def get_next(self, source, reverse=False):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        tab_order = savvy_settings.get("tab_order")

        if reverse is True:
            tab_order.reverse()

        source_idx = tab_order.index(source)
        next_idx = 0 if source_idx == len(tab_order) - 1 else source_idx + 1

        return tab_order[next_idx]
