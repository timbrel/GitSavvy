import sys
import imp

import sublime
from sublime_plugin import WindowCommand


class GsReloadModulesDebug(WindowCommand):

    def run(self):
        gadget_settings = sublime.load_settings("GitSavvy.sublime-settings")

        if gadget_settings.get("dev_mode"):
            for _ in range(2):
                for name, module in sys.modules.items():
                    if name[0:9] == "GitSavvy":
                        print("GitSavvy: reloading submodule", name)
                        imp.reload(module)
