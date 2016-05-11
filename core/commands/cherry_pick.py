import sublime
from sublime_plugin import TextCommand

from .log import GsLogCommand
from ..git_mixins.branches import BranchesMixin
from ...common import util


class GsCherryPickCommand(GsLogCommand, BranchesMixin):
    def run_async(self):
        if self._target_hash:
            return self.cherry_pick(self._target_hash)

        self.select_commit = super().run_async

        self.all_branches = [b.name_with_remote for b in self.get_branches()]
        self.window.show_quick_panel(
            self.all_branches,
            self.on_branch_selection,
            flags=sublime.MONOSPACE_FONT
        )

    def on_branch_selection(self, index):
        if index == -1:
            return

        self.cherry_branch = self.all_branches[index]
        return self.select_commit()

    def on_hash_selection(self, index):
        if index == -1:
            return
        if index == self._limit:
            self._pagination += self._limit
            sublime.set_timeout_async(lambda: self.select_commit(), 1)
            return
        self.cherry_pick(self._hashes[index])

    def cherry_pick(self, commit_hash):
        self.git("cherry-pick", commit_hash)
        sublime.status_message("Commit %s cherry-picked successfully." %
                               commit_hash)
        util.view.refresh_gitsavvy(self.window.active_view())
