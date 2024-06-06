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
    @on_worker
    def do_action(self, commit_hash, **kwargs):
        try:
            self.git("revert", *(commit_hash if isinstance(commit_hash, list) else [commit_hash]))
        finally:
            util.view.refresh_gitsavvy(self.window.active_view(), refresh_sidebar=True)


class gs_revert_abort(GsWindowCommand):
    @on_worker
    def run(self):
        try:
            self.git("revert", "--abort")
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_revert_continue(GsWindowCommand):
    @on_worker
    def run(self):
        try:
            self.git("revert", "--continue")
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_revert_skip(GsWindowCommand):
    @on_worker
    def run(self):
        try:
            self.git("revert", "--skip")
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)
