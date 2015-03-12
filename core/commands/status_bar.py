import sublime
from sublime_plugin import TextCommand, EventListener

from ..git_command import GitCommand


class GsStatusBarEventListener(EventListener):

    def on_new(self, view):
        view.run_command("gs_update_status_bar")

    def on_load(self, view):
        view.run_command("gs_update_status_bar")

    def on_activated(self, view):
        view.run_command("gs_update_status_bar")

    def on_post_save(self, view):
        view.run_command("gs_update_status_bar")


class GsUpdateStatusBarCommand(TextCommand, GitCommand):

    """
    Update the short Git status in the Sublime status bar.
    """

    def run(self, edit):
        if sublime.load_settings("GitSavvy.sublime-settings").get("git_status_in_status_bar"):
            sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        # Short-circuit update attempts for files not part of Git repo.
        if not self.file_path or not self._repo_path(throw_on_stderr=False):
            self.view.erase_status("gitsavvy-repo-status")
            return

        short_status = self.get_branch_status_short()
        self.view.set_status("gitsavvy-repo-status", short_status)
