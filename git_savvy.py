import importlib.abc
import importlib.machinery
import sys
from types import ModuleType


# kiss-reloader:
class InPlaceReloader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, package_name=__spec__.parent, plugin_name=__name__):
        prefix = package_name + "."
        self.modules = {
            name: module
            for name, module in sys.modules.items()
            if name.startswith(prefix) and name != plugin_name
        }
        self.loaders = {}

    def __enter__(self):
        return self.install()

    def __exit__(self, exc_type, exc_value, traceback):
        self.uninstall()

    def install(self):
        for name in self.modules:
            sys.modules.pop(name, None)

        self.clear_parent_module_attributes()
        sys.meta_path.insert(0, self)
        return self

    def uninstall(self):
        if self in sys.meta_path:
            sys.meta_path.remove(self)

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self.modules:
            return None

        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None

        self.loaders[fullname] = spec.loader
        spec.loader = self
        return spec

    def create_module(self, spec):
        return self.modules[spec.name]

    def exec_module(self, module):
        self.loaders[module.__name__].exec_module(module)

    def clear_parent_module_attributes(self):
        for name, module in self.modules.items():
            parent_name, _, attr = name.rpartition(".")
            parent = self.modules.get(parent_name)
            if isinstance(parent, ModuleType) and getattr(parent, attr, None) is module:
                delattr(parent, attr)


with InPlaceReloader():
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
