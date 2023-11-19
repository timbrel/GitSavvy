import sublime

from .common.commands import *
from .common.ui import *
from .common.global_events import *
from .core.commands import *
from .core.settings import *
from .core.interfaces import *
from .core.runtime import *
from .github.commands import *
from .gitlab.commands import *


def plugin_loaded():

    try:
        import package_control.events
    except ImportError:
        pass
    else:
        if (
            package_control.events.install('GitSavvy') or
            package_control.events.post_upgrade('GitSavvy')
        ):
            # The "event" (flag) is set for 5 seconds. To not get into a
            # reloader excess we wait for that time, so that the next time
            # this exact `plugin_loaded` handler runs, the flag is already
            # unset.
            sublime.set_timeout_async(reload_plugin, 5000)
            return

    prepare_gitsavvy()


def reload_plugin():
    from .common import util
    print("GitSavvy: Reloading plugin after install.")
    util.reload.reload_plugin(verbose=False, then=prepare_gitsavvy)


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
