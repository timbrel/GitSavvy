"""
Sublime commands related to development and debugging.
"""

import sys
import imp

import sublime
from sublime_plugin import WindowCommand


class GsReloadModulesDebug(WindowCommand):

    """
    When triggered, reload all GitSavvy submodules twice, so as not
    to worry about load order.
    """

    def run(self):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")

        if savvy_settings.get("dev_mode"):
            for _ in range(2):
                for name, module in sys.modules.items():
                    if name[0:8] == "GitSavvy":
                        print("GitSavvy: reloading submodule", name)
                        imp.reload(module)

            sublime.sublime_api.plugin_host_ready()
