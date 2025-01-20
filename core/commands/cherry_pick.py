import sublime

from .log import gs_log_by_branch
from ...common import util
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.runtime import on_worker


__all__ = (
    "gs_cherry_pick",
    "gs_cherry_pick_abort",
    "gs_cherry_pick_continue",
    "gs_cherry_pick_skip",
)


class gs_cherry_pick(gs_log_by_branch):

    def log(self, **kwargs):  # type: ignore[override]
        kwargs["cherry"] = True
        kwargs["start_end"] = ("", kwargs["branch"])
        return super().log(**kwargs)

    def do_action(self, commit_hash, **kwargs):
        self.git("cherry-pick", commit_hash)
        sublime.active_window().status_message("Commit %s cherry-picked successfully." % commit_hash)
        util.view.refresh_gitsavvy(self.window.active_view())


class gs_cherry_pick_abort(GsWindowCommand):
    @on_worker
    def run(self):
        self.git("cherry-pick", "--abort")
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_cherry_pick_continue(GsWindowCommand):
    @on_worker
    def run(self):
        self.git("cherry-pick", "--continue")
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_cherry_pick_skip(GsWindowCommand):
    @on_worker
    def run(self):
        self.git("cherry-pick", "--skip")
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)
