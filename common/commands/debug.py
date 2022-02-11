"""
Sublime commands related to development and debugging.
"""

from sublime_plugin import WindowCommand

from ..util import debug, reload
from ...core.settings import GitSavvySettings
from ...core.view import replace_view_content


__all__ = (
    "gs_reload_modules_debug",
    "gs_start_logging",
    "gs_stop_logging",
    "gs_view_git_log",
)


class gs_reload_modules_debug(WindowCommand):

    """
    When triggered, reload all GitSavvy submodules.
    """

    def run(self):
        reload.reload_plugin()

    def is_visible(self):
        return GitSavvySettings().get("dev_mode")


class gs_start_logging(WindowCommand):

    """
    Starts recording all interactions with Git for reporting issues.
    """

    def run(self):
        debug.start_logging()


class gs_stop_logging(WindowCommand):

    """
    Stops recording interactions with Git.
    """

    def run(self):
        debug.stop_logging()


class gs_view_git_log(WindowCommand):

    """
    Displays the recent recording.
    """

    def run(self):
        log = debug.get_log()
        view = self.window.new_file()
        view.set_scratch(True)
        view.settings().set("syntax", "Packages/JavaScript/JSON.sublime-syntax")
        replace_view_content(view, log)
