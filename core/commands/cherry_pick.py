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

    @on_worker
    def do_action(self, commit_hash, **kwargs):
        commit_hashes = commit_hash if isinstance(commit_hash, list) else [commit_hash]
        try:
            self.git("cherry-pick", *commit_hashes)
            label = ", ".join(commit_hashes)
            s = "" if len(commit_hashes) == 1 else "s"
            self.window.status_message(
                f"Commit{s} {label} cherry-picked successfully."
            )
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


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
