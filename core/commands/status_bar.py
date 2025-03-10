import sublime
from sublime_plugin import TextCommand, EventListener

from ..git_command import GitCommand
from ..runtime import run_when_worker_is_idle, throttled
from GitSavvy.core import store


STATUSBAR_KEY = "gitsavvy-repo-status"


class GsStatusBarEventListener(EventListener):
    def on_activated(self, view):
        view.run_command("gs_draw_status_bar")

    def on_post_save(self, view):
        view.run_command("gs_update_status")


class gs_update_status(TextCommand, GitCommand):
    def run(self, edit):
        run_when_worker_is_idle(throttled(self.run_impl, self.view))

    def run_impl(self, view):
        repo_path = self.find_repo_path()
        if repo_path:
            try:
                self.update_working_dir_status()
            except RuntimeError:
                # Although with `if repo_path` we have enough to make the
                # status call to git safe, the processing of the status
                # asks `self.repo_path` multiple times.
                # A user might have closed the view in between so we MUST
                # catch potential `RuntimeError`s.
                pass


class gs_draw_status_bar(TextCommand, GitCommand):

    """
    Update the short Git status in the Sublime status bar.
    """

    def run(self, edit, repo_path=None):
        view = self.view
        if not self.savvy_settings.get("git_status_in_status_bar"):
            view.erase_status(STATUSBAR_KEY)
            return

        if not repo_path:
            repo_path = self.find_repo_path()
            if not repo_path:
                return
        elif repo_path != self.find_repo_path():
            return

        try:
            short_status = store.current_state(repo_path)["short_status"]
        except Exception:
            ...
        else:
            view.set_status(STATUSBAR_KEY, short_status)


def on_status_update(repo_path, state):
    view = sublime.active_window().active_view()
    if view:
        view.run_command("gs_draw_status_bar", {"repo_path": repo_path})


store.subscribe("*", {"short_status"}, on_status_update)
