import sys
prefix = __package__ + '.'  # don't clear the base package
for module_name in [
        module_name for module_name in sys.modules
        if module_name.startswith(prefix) and module_name != __name__]:
    del sys.modules[module_name]
del prefix


import sublime  # noqa: E402

from .common.commands import *  # noqa: E402
from .common.ui import *  # noqa: E402
from .common.global_events import *  # noqa: E402
from .core.commands import *  # noqa: E402
from .core.settings import *  # noqa: E402
from .core.interfaces import *  # noqa: E402
from .core.runtime import *  # noqa: E402
from .github.commands import *  # noqa: E402
from .gitlab.commands import *  # noqa: E402


def plugin_loaded():
    prepare_gitsavvy()


def prepare_gitsavvy():
    from .common import util
    from .core import runtime
    runtime.determine_thread_names()
    sublime.set_timeout_async(util.file.determine_syntax_files)

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
