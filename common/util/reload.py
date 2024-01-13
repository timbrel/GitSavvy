import sublime
import sublime_plugin
import os
import posixpath
import threading
import builtins
import functools
import importlib
import sys
import traceback
from inspect import ismodule
from contextlib import contextmanager
from .debug import StackMeter


from typing import Dict
from types import ModuleType


def reload_plugin(verbose=True, then=None):
    threading.Thread(
        target=functools.partial(reload_package, 'GitSavvy', verbose=verbose, then=then)
    ).start()


def dprint(*args, fill=None, fill_width=60, **kwargs):
    if fill is not None:
        sep = str(kwargs.get('sep', ' '))
        caption = sep.join(args)
        args = "{0:{fill}<{width}}".format(caption and caption + sep,
                                           fill=fill, width=fill_width),
    print("[Package Reloader]", *args, **kwargs)


def path_contains(a, b):
    return a == b or b.startswith(a + os.sep)


def get_package_modules(pkg_name):
    # type: (str) -> Dict[str, ModuleType]
    in_installed_path = functools.partial(
        path_contains,
        os.path.join(
            sublime.installed_packages_path(),
            pkg_name + '.sublime-package'
        )
    )

    in_package_path = functools.partial(
        path_contains,
        os.path.join(sublime.packages_path(), pkg_name)
    )

    def module_in_package(module):
        # Other (extracted) ST plugins using python 3.8 have this set to
        # `None` surprisingly.
        file = getattr(module, '__file__', None) or ''
        paths = getattr(module, '__path__', ())
        return (
            in_installed_path(file) or any(map(in_installed_path, paths)) or
            in_package_path(file) or any(map(in_package_path, paths))
        )

    return {
        name: module
        for name, module in sys.modules.items()
        if module_in_package(module)
    }


def package_plugins(pkg_name):
    return [
        pkg_name + '.' + posixpath.basename(posixpath.splitext(path)[0])
        for path in sublime.find_resources("*.py")
        if posixpath.dirname(path) == 'Packages/' + pkg_name
    ]


def reload_package(pkg_name, dummy=True, verbose=True, then=None):
    if pkg_name not in sys.modules:
        dprint("error:", pkg_name, "is not loaded.")
        return

    if verbose:
        dprint("begin", fill='=')

    all_modules = {
        module_name: module
        for module_name, module in get_package_modules(pkg_name).items()
    }
    plugins = [plugin for plugin in package_plugins(pkg_name)]

    # Tell Sublime to unload plugins
    for plugin in plugins:
        module = sys.modules.get(plugin)
        if module:
            sublime_plugin.unload_module(module)

    # Unload modules
    for module_name in all_modules:
        sys.modules.pop(module_name)

    # Reload packages
    try:
        with intercepting_imports(all_modules, verbose), importing_fromlist_aggressively(all_modules):
            for plugin in plugins:
                sublime_plugin.reload_plugin(plugin)
    except Exception:
        dprint("reload failed.", fill='-')
        # Rollback modules
        for name, module in all_modules.items():
            sys.modules[name] = module

        # Try reloading again to get the commands back. Here esp. the
        # reload command itself.
        for plugin in plugins:
            sublime_plugin.reload_plugin(plugin)

        traceback.print_exc()
        print('--- Reloading GitSavvy failed. Restarting Sublime is highly recommended. ---')
        sublime.active_window().status_message('GitSavvy reloading 💣ed. 😒.')
        return

    if dummy:
        load_dummy(verbose)

    if verbose:
        dprint("end", fill='-')

    if then:
        then()

    sublime.active_window().status_message('GitSavvy has 🙌 reloaded.')


def load_dummy(verbose):
    """
    Hack to trigger automatic "reloading plugins".

    This is needed to ensure TextCommand's and WindowCommand's are ready.
    """
    if verbose:
        dprint("installing dummy package")

    dummy = "User._dummy"
    dummy_py = os.path.join(sublime.packages_path(), "User", "_dummy.py")
    with open(dummy_py, "w"):
        pass

    def remove_dummy(trial=0):
        if dummy in sys.modules:
            if verbose:
                dprint("removing dummy package")
            try:
                os.unlink(dummy_py)
            except FileNotFoundError:
                pass
            after_remove_dummy()
        elif trial < 300:
            threading.Timer(0.1, lambda: remove_dummy(trial + 1)).start()
        else:
            try:
                os.unlink(dummy_py)
            except FileNotFoundError:
                pass

    condition = threading.Condition()

    def after_remove_dummy(trial=0):
        if dummy not in sys.modules:
            condition.acquire()
            condition.notify()
            condition.release()
        elif trial < 300:
            threading.Timer(0.1, lambda: after_remove_dummy(trial + 1)).start()

    threading.Timer(0.1, remove_dummy).start()
    condition.acquire()
    condition.wait(30)  # 30 seconds should be enough for all regular usages
    condition.release()


@contextmanager
def intercepting_imports(modules, verbose):
    finder = FilterFinder(modules, verbose)
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)


@contextmanager
def importing_fromlist_aggressively(modules):
    orig___import__ = builtins.__import__

    @functools.wraps(orig___import__)
    def __import__(name, globals=None, locals=None, fromlist=(), level=0):
        module = orig___import__(name, globals, locals, fromlist, level)
        if fromlist and module.__name__ in modules:
            if '*' in fromlist:
                fromlist = list(fromlist)
                fromlist.remove('*')
                fromlist.extend(getattr(module, '__all__', []))
            for x in fromlist:
                if ismodule(getattr(module, x, None)):
                    from_name = '{}.{}'.format(module.__name__, x)
                    if from_name in modules:
                        importlib.import_module(from_name)
        return module

    builtins.__import__ = __import__
    try:
        yield
    finally:
        builtins.__import__ = orig___import__


class FilterFinder(importlib.abc.MetaPathFinder):
    def __init__(self, modules, verbose):
        self._modules = modules
        self._stack_meter = StackMeter()
        self._verbose = verbose

    def find_module(self, name, path=None):
        if name in self._modules:
            return self

    def load_module(self, name):
        module = self._modules[name]
        sys.modules[name] = module  # restore the module back
        with self._stack_meter as depth:
            if self._verbose:
                dprint("reloading", ('| ' * depth) + '|--', name)
            try:
                return module.__loader__.load_module(name)
            except Exception:
                if name in sys.modules:
                    del sys.modules[name]  # to indicate an error
                raise
