import sys

import sublime

if sys.version_info[0] == 2:
    raise ImportWarning("GitSavvy does not support Sublime Text 2.")
else:
    def plugin_loaded():
        from .common import util
        sublime.set_timeout_async(util.file.determine_syntax_files)

        # Ensure all interfaces are ready.
        sublime.set_timeout_async(
            lambda: util.view.refresh_gitsavvy(sublime.active_window().active_view()))

        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        if savvy_settings.get("load_additional_codecs"):
            sublime.set_timeout_async(reload_codecs, 0)

    def reload_codecs():
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        fallback_encoding = savvy_settings.get("fallback_encoding")
        try:
            import _multibytecodec, imp, codecs, encodings
            imp.reload(encodings)
            imp.reload(codecs)
            codecs.getencoder(fallback_encoding)
        except (ImportError, LookupError):
            sublime.error_message(
                "You have enabled `load_additional_codecs` mode, but the "
                "`fallback_encoding` codec cannot load.  This probably means "
                "you don't have the Codecs33 package installed, or you've "
                "entered an unsupported encoding.")

    from .common.commands import *
    from .common.ui import *
    from .common.global_events import *
    from .core.commands import *
    from .core.interfaces import *
    from .github.commands import *
    from .gitlab.commands import *
