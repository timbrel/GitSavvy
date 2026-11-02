import sublime


from .stash import *
from .status import *  # noqa: F401, F403
from .status_main_actions import *
from .blame import *
from .branch import *  # noqa: F401, F403
from .commit import *
from .line_history import *
from .remotes import *  # noqa: F401, F403
from .rebase import *  # noqa: F401, F403
from .tags import *  # noqa: F401, F403
from GitSavvy.core.base_commands import GsTextCommand as _GsTextCommand
from ..git_command import GitCommand


class gs_tab_cycle(_GsTextCommand):
    commands = {
        "status": "gs_show_status",
        "branch": "gs_show_branch",
        "remotes": "gs_show_remotes",
        "rebase": "gs_show_rebase",
        "tags": "gs_show_tags",
        "graph": "gs_log_graph_tab_in"
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
        tab_order = self.app_settings.get("tab_order")

        try:
            idx = tab_order.index(source)
        except ValueError:
            return None

        delta = (-1 if reverse else +1)
        next_idx = (idx + delta) % len(tab_order)
        return tab_order[next_idx]
