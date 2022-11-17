from sublime_plugin import WindowCommand

from .log import LogMixin
from ..git_command import GitCommand
from ...common import util
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.runtime import on_worker


__all__ = (
    "gs_revert_commit",
    "gs_revert_abort",
    "gs_revert_continue",
    "gs_revert_skip",
)


class gs_revert_commit(LogMixin, WindowCommand, GitCommand):
    def run_async(self, **kwargs):
        if "commit_hash" in kwargs:
            commit_hash = kwargs["commit_hash"]
            self.do_action(commit_hash)
        else:
            super().run_async(**kwargs)

    def do_action(self, commit_hash, **kwargs):
        self.git("revert", commit_hash)
        util.view.refresh_gitsavvy(self.window.active_view(), refresh_sidebar=True)


class gs_revert_abort(GsWindowCommand):
    @on_worker
    def run(self):
        self.git("revert", "--abort")
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_revert_continue(GsWindowCommand):
    @on_worker
    def run(self):
        self.git("revert", "--continue")
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_revert_skip(GsWindowCommand):
    @on_worker
    def run(self):
        self.git("revert", "--skip")
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)
