import importlib
import sys
from contextlib import contextmanager

import sublime_plugin


def reload_plugin():
    """Reload the GitSavvy plugin among with all its modules."""
    from GitSavvy import git_savvy

    modules = {name: module for name, module in sys.modules.items()
               if name.startswith("GitSavvy.")}
    try:
        reload_modules(git_savvy, modules)
    finally:
        ensure_loaded(git_savvy, modules)


def ensure_loaded(main, modules):
    # Ensures all modules were put back into the sys.modules registry. This
    # keeps the plugin at least in a partially working state, even if some of
    # the modules failed to reload.
    missing_modules = {name: module for name, module in modules.items()
                       if name not in sys.modules}
    if missing_modules:
        for name, module in missing_modules:
            sys.modules[name] = modules
            print("NO RELOAD", name)
        sublime_plugin.reload_plugin(git_savvy.__name__)


def reload_modules(main, modules):
    """Implements the machinery for reloading a given plugin module."""
    #
    # Here's the approach in general:
    #
    #   - Hide GitSavvy modules from the sys.modules temporarily;
    #
    #   - Install a special import hook onto sys.meta_path;
    #
    #   - Call sublime_plugin.reload_plugin(), which imports the main
    #     "git_savvy" module under the hood, triggering the hook;
    #
    #   - The hook, instead of creating a new module object, peeks the saved
    #     one and reloads it. Once the module encounters an import statement
    #     requesting another module, not yet reloaded, the hook reenters and
    #     processes that new module recursively, then get back to the previous
    #     one, and so on.
    #
    # This makes the modules reload in the very same order as they were loaded
    # initially, as if they were imported from scratch.
    #
    sublime_plugin.unload_module(main)

    # Insert the main "git_savvy" module at the beginning to make the reload
    # order be as close to the order of the "natural" import as possible.
    module_names = [main.__name__] + sorted(name for name in modules
                                            if name != main.__name__)

    # First, remove all the loaded modules from the sys.modules cache,
    # otherwise the reloading hook won't be called.
    loaded_modules = dict(sys.modules)
    for name in loaded_modules:
        if name in modules:
            del sys.modules[name]

    @FilteringImportHook.when(condition=lambda name: name in modules)
    def module_reloader(name):
        module = modules[name]
        sys.modules[name] = module  # restore the module back
        print("reloading", name)
        return module.__loader__.load_module(name)

    with intercepting_imports(module_reloader):
        # Now, import all the modules back, in order, starting with the main
        # module. This will reload all the modules directly or indirectly
        # referenced by the main one, i.e. usually most of our modules.
        sublime_plugin.reload_plugin(main.__name__)

        # Be sure to bring back *all* the modules that used to be loaded, not
        # only these imported through the main one. Otherwise, some of them
        # might end up being created from scratch as new module objects in
        # case of being imported after detaching the hook. In general, most of
        # the imports below (if not all) are no-ops though.
        for name in module_names:
            importlib.import_module(name)


@contextmanager
def intercepting_imports(hook):
    sys.meta_path.insert(0, hook)
    try:
        yield hook
    finally:
        if hook in sys.meta_path:
            sys.meta_path.remove(hook)


class FilteringImportHook:
    """
    PEP-302 importer that delegates loading of given modules to a function.
    """

    def __init__(self, condition, load_module):
        super().__init__()
        self.condition = condition
        self.load_module = load_module

    @classmethod
    def when(cls, condition):
        """A handy loader function decorator."""
        return lambda load_module: cls(condition, load_module)

    def find_module(self, name, path=None):
        if self.condition(name):
            return self
