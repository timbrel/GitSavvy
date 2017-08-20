from sublime_plugin import WindowCommand

from .log import LogMixin
from ..git_command import GitCommand
from ...common import util


class GsRevertCommitCommand(LogMixin, WindowCommand, GitCommand):

    def run_async(self, **kwargs):
        if "commit_hash" in kwargs:
            commit_hash = kwargs["commit_hash"]
            self.do_action(commit_hash)
        else:
            super().run_async(**kwargs)

    def do_action(self, commit_hash, **kwargs):
        self.git("revert", commit_hash)
        util.view.refresh_gitsavvy(self.window.active_view(), refresh_sidebar=True)
