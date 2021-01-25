import sublime
from sublime_plugin import TextCommand, EventListener

from ..git_command import GitCommand
from ..runtime import enqueue_on_worker, throttled
from GitSavvy.core import store


class GsStatusBarEventListener(EventListener):
    def on_activated(self, view):
        view.run_command("gs_draw_status_bar")

    def on_post_save(self, view):
        view.run_command("gs_update_status")


def view_is_transient(view):
    """Return whether a view can be considered 'transient'.

    We especially want to exclude widgets and preview views.
    """

    # 'Detached' (already closed) views and previews don't have
    # a window.
    window = view.window()
    if not window:
        return True

    if view.settings().get('is_widget'):
        return True

    return False


class gs_update_status(TextCommand, GitCommand):
    def run(self, edit):
        enqueue_on_worker(throttled(self.run_impl, self.view))

    def run_impl(self, view):
        if view_is_transient(view):
            return

        repo_path = self.find_repo_path()
        if repo_path:
            try:
                self.get_working_dir_status()
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
        if not self.savvy_settings.get("git_status_in_status_bar"):
            return

        if not repo_path:
            repo_path = self.find_repo_path()
            if not repo_path:
                return
        elif repo_path != self.find_repo_path():
            return

        try:
            short_status = store.current_state(repo_path)["status"].short_status
        except Exception:
            ...
        else:
            self.view.set_status("gitsavvy-repo-status", short_status)


def on_status_update(repo_path, state):
    view = sublime.active_window().active_view()
    if view:
        view.run_command("gs_draw_status_bar", {"repo_path": repo_path})


store.subscribe("*", {"status"}, on_status_update)
