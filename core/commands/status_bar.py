from sublime_plugin import TextCommand, EventListener

from ..git_command import GitCommand
from ..runtime import enqueue_on_worker, throttled


class GsStatusBarEventListener(EventListener):
    # Note: `on_activated` is registered in global_events.py
    def on_new(self, view):
        view.run_command("gs_update_status_bar")

    def on_post_save(self, view):
        view.run_command("gs_update_status_bar")


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


class GsUpdateStatusBarCommand(TextCommand, GitCommand):

    """
    Update the short Git status in the Sublime status bar.
    """

    def run(self, edit):
        enqueue_on_worker(throttled(self.run_impl, self.view))

    def run_impl(self, view):
        if view_is_transient(view):
            return

        if not self.savvy_settings.get("git_status_in_status_bar"):
            return

        try:
            # Explicitly check `find_repo_path` first which does not offer
            # automatic initialization on failure.
            repo_path = self.find_repo_path()
            short_status = self.get_branch_status_short() if repo_path else ""
        except Exception:
            short_status = ""
        view.set_status("gitsavvy-repo-status", short_status)
