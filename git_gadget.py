import sys
import sublime
import imp
from .common import log

log.set_level(0)

if sys.version_info[0] == 2:
    raise ImportWarning("GitGadget does not support Sublime Text 2.")
else:
    def plugin_loaded():
        gadget_settings = sublime.load_settings("GitGadget.sublime-settings")

        if gadget_settings.get("dev_mode"):
            # Reload all submodules when debugging.
            for _ in range(2):
                for name, module in sys.modules.items():
                    if name[0:9] == "GitGadget":
                        print("reloading " + name)
                        imp.reload(module)

        from .common import util
        util.determine_syntax_files()

    from .commands import *
