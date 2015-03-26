"""
Sublime commands related to development and debugging.
"""

import sys
import imp
import urllib

import sublime
from sublime_plugin import WindowCommand

from ..util import debug

REPORT_URL_TEMPLATE = "https://github.com/divmain/GitSavvy/issues/new?{q}"


class GsReloadModulesDebug(WindowCommand):

    """
    When triggered, reload all GitSavvy submodules twice, so as not
    to worry about load order.
    """

    def run(self):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")

        if savvy_settings.get("dev_mode"):
            for _ in range(2):
                for name in self.ordered_modules():
                    module = sys.modules[name]
                    print("GitSavvy: reloading submodule", name)
                    imp.reload(module)

            sublime.sublime_api.plugin_host_ready()

    def ordered_modules(self):
        # common.ui must always be loaded first because it sets up the
        # array of interface listeners.  If it's loaded after any of the
        # interface modules then refresh events will be broken.
        modules = ["GitSavvy.common.ui"]
        modules += [mod for mod in sys.modules.keys()
                    if mod[0:8] == "GitSavvy" and mod not in modules]

        return modules


class GsStartLoggingCommand(WindowCommand):

    """
    Starts recording all interactions with Git for reporting issues.
    """

    def run(self):
        debug.start_logging()


class GsStopLoggingCommand(WindowCommand):

    """
    Stops recording interactions with Git.
    """

    def run(self):
        debug.stop_logging()


class GsViewGitLog(WindowCommand):

    """
    Displays the recent recording.
    """

    def run(self):
        log = debug.get_log()
        view = self.window.new_file()
        view.set_scratch(True)
        view.settings().set("syntax", "Packages/JavaScript/JSON.tmLanguage")
        view.run_command("gs_replace_view_text", {
            "text": log,
            "nuke_cursors": True
            })
