import time
import sublime
from sublime_plugin import TextCommand, EventListener

from ..git_command import GitCommand
from ...common.util import debug


class GsStatusBarEventListener(EventListener):

    def on_new(self, view):
        view.run_command("gs_update_status_bar")

    def on_load(self, view):
        view.run_command("gs_update_status_bar")

    def on_activated(self, view):
        view.run_command("gs_update_status_bar")

    def on_post_save(self, view):
        view.run_command("gs_update_status_bar")


last_execution = 0
update_status_bar_soon = False


class GsUpdateStatusBarCommand(TextCommand, GitCommand):

    """
    Update the short Git status in the Sublime status bar.
    """

    def run(self, edit):
        global last_execution, update_status_bar_soon
        if sublime.load_settings("GitSavvy.sublime-settings").get("git_status_in_status_bar"):

            millisec = int(round(time.time() * 1000))
            # If we updated to less then 100 ms we don't need to update now but
            # should update in 100 ms in case of current file change.
            #
            # So if this get called 4 timer with 20 ms in between each call
            # it will only update twice. Once at time 0 and one at time 100
            # even if it got called at 0, 20, 40, 60 and 80.

            if millisec - 100 > last_execution:
                sublime.set_timeout_async(self.run_async, 0)
            else:
                if not update_status_bar_soon:
                    update_status_bar_soon = True
                    sublime.set_timeout_async(self.run_async, 100)

            last_execution = int(round(time.time() * 1000))

    def run_async(self):
        # Short-circuit update attempts for files not part of Git repo.
        if not self._repo_path(throw_on_stderr=False):
            self.view.erase_status("gitsavvy-repo-status")
            return

        with debug.disable_logging():
            short_status = self.get_branch_status_short()
        self.view.set_status("gitsavvy-repo-status", short_status)

        global update_status_bar_soon
        update_status_bar_soon = False
