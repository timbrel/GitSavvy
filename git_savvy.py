import importlib
import sys

# kiss-reloader:
prefix = __spec__.parent + "."  # type: ignore[operator]  # don't reload the base package
modules = [
    module
    for module_name, module in sys.modules.items()
    if module_name.startswith(prefix) and module_name != __name__
]
for module in modules:
    importlib.reload(module)
for module in modules:
    importlib.reload(module)


import sublime

from .common.commands import *
from .common.ui import *
from .common.global_events import *
from .core.commands import *
from .core.settings import *
from .core.interfaces import *
from .core.runtime import *
from .core.caches import *
from .github.commands import *
from .gitlab.commands import *


def prepare_gitsavvy():
    from .common import util
    from .core import runtime
    runtime.determine_thread_names()

    # Ensure all interfaces are ready.
    sublime.set_timeout_async(
        lambda: util.view.refresh_gitsavvy(sublime.active_window().active_view()))

    savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
    if savvy_settings.get("load_additional_codecs"):
        sublime.set_timeout_async(reload_codecs)


def reload_codecs():
    savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
    fallback_encoding = savvy_settings.get("fallback_encoding")
    try:
        import imp, codecs, encodings
        imp.reload(encodings)
        imp.reload(codecs)
        codecs.getencoder(fallback_encoding)
    except (ImportError, LookupError):
        sublime.error_message(
            "You have enabled `load_additional_codecs` mode, but the "
            "`fallback_encoding` codec cannot load.  This probably means "
            "you don't have the Codecs33 package installed, or you've "
            "entered an unsupported encoding.")


prepare_gitsavvy()
