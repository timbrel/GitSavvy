

from .log import LogMixin

from ..interfaces import util as interface_util
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.runtime import on_worker


__all__ = (
    "gs_revert_commit",
    "gs_revert_abort",
    "gs_revert_continue",
    "gs_revert_skip",
)


class gs_revert_commit(LogMixin, GsWindowCommand):
    @on_worker
    def do_action(self, commit_hash, **kwargs):
        try:
            self.git("revert", *(commit_hash if isinstance(commit_hash, list) else [commit_hash]))
        finally:
            interface_util.refresh_gitsavvy(self.window.active_view(), refresh_sidebar=True)


class gs_revert_abort(GsWindowCommand):
    @on_worker
    def run(self):
        try:
            self.git("revert", "--abort")
        finally:
            interface_util.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_revert_continue(GsWindowCommand):
    @on_worker
    def run(self):
        try:
            self.git("revert", "--continue")
        finally:
            interface_util.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_revert_skip(GsWindowCommand):
    @on_worker
    def run(self):
        try:
            self.git("revert", "--skip")
        finally:
            interface_util.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)
