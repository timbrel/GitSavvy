from functools import lru_cache
import os

import sublime
import sublime_plugin

from GitSavvy.core.fns import maybe


__all__ = (
    "ProjectFileChanges",
)

from typing import Dict


class GitSavvySettings:
    def __init__(self, window=None):
        # type: (sublime.Window) -> None
        self._window = window or sublime.active_window()
        self._global_settings = get_global_settings()

    def get(self, key, default=None):
        try:
            return get_project_settings(self._window)[key]
        except KeyError:
            return self._global_settings.get(key, default)

    def set(self, key, value):
        self._global_settings.set(key, value)


CHANGE_COUNT = 0


class ProjectFileChanges(sublime_plugin.EventListener):
    def on_post_save(self, view):
        # type: (sublime.View) -> None
        global CHANGE_COUNT
        file_path = view.file_name()
        if file_path and file_path.endswith(".sublime-project"):
            CHANGE_COUNT += 1


def get_project_settings(window):
    # type: (sublime.Window) -> Dict
    global CHANGE_COUNT
    return _get_project_settings(window.id(), CHANGE_COUNT)


@lru_cache(maxsize=16)
def _get_project_settings(wid, _counter):
    # type: (sublime.WindowId, int) -> Dict
    window = sublime.Window(wid)
    project_data = window.project_data()
    if not project_data:
        return {}
    return project_data.get("settings", {}).get("GitSavvy", {})


@lru_cache(maxsize=1)
def get_global_settings():
    return GlobalSettings("GitSavvy.sublime-settings")


class GlobalSettings:
    def __init__(self, name):
        self._settings = s = sublime.load_settings(name)
        s.clear_on_change(name)
        s.add_on_change(name, self._on_update)
        self._cache = {}

    def get(self, name, default=None):
        try:
            return self._cache[name]
        except KeyError:
            self._cache[name] = current_value = self._settings.get(name, default)  # type: ignore[var-annotated]
            return current_value

    def set(self, name, value):
        self._settings.set(name, value)  # implicitly calls `_on_update` to clear cache

    def _on_update(self):
        self._cache.clear()


class SettingsMixin:
    _savvy_settings = None

    @property
    def savvy_settings(self):
        if not self._savvy_settings:
            window = self.some_window()
            self._savvy_settings = GitSavvySettings(window)
        return self._savvy_settings

    def some_window(self):
        # type: () -> sublime.Window
        return (
            maybe(lambda: self.window)  # type: ignore[attr-defined]
            or maybe(lambda: self.view.window())  # type: ignore[attr-defined]
            or sublime.active_window()
        )

    def default_project_root(self) -> str:
        default_root = self.savvy_settings.get("users_home") or "~"
        if os.name == "nt" and default_root == "~":
            default_root = R"~\Desktop"
        return os.path.expanduser(default_root)
