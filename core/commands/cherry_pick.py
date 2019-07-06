import sublime

from .log import GsLogByBranchCommand
from ...common import util


class GsCherryPickCommand(GsLogByBranchCommand):

    def log(self, **kwargs):
        kwargs["cherry"] = True
        kwargs["start_end"] = ("", kwargs["branch"])
        return super().log(**kwargs)

    def do_action(self, commit_hash, **kwargs):
        self.git("cherry-pick", commit_hash)
        sublime.active_window().status_message("Commit %s cherry-picked successfully." % commit_hash)
        util.view.refresh_gitsavvy(self.window.active_view())
