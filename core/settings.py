from __future__ import annotations

from functools import lru_cache
import os
from typing import Any

import sublime
import sublime_plugin

from GitSavvy.core.fns import maybe


__all__ = (
    "ProjectFileChanges",
    "ProjectSettings",
    "app_settings",
)


class _AppSettings:
    def __init__(self) -> None:
        self._settings = settings = sublime.load_settings("GitSavvy.sublime-settings")
        self._cache: dict[str, Any] = {}
        settings.clear_on_change(__name__)
        settings.add_on_change(__name__, self._on_update)

    def get(self, name: str, default: Any = None) -> Any:
        try:
            return self._cache[name]
        except KeyError:
            current_value: Any = self._settings.get(name, default)
            self._cache[name] = current_value
            return current_value

    def set(self, name: str, value: Any) -> None:
        self._settings.set(name, value)  # implicitly calls `_on_update` to clear cache

    def has(self, name: str) -> bool:
        return self._settings.has(name)

    def _on_update(self) -> None:
        self._cache.clear()


app_settings = _AppSettings()


class ProjectSettings:
    _instances: dict[sublime.WindowId, ProjectSettings] = {}
    _window: sublime.Window
    _change_count: int
    _settings: dict[str, Any]

    def __new__(cls, window: sublime.Window) -> ProjectSettings:
        window_id = window.id()
        instance = cls._instances.get(window_id)
        if instance is None:
            instance = super().__new__(cls)
            instance._window = window
            instance._change_count = -1
            instance._settings = {}
            cls._instances[window_id] = instance
        return instance

    def __init__(self, window: sublime.Window) -> None:
        pass

    def get(self, key: str, default: Any = None) -> Any:
        if self._change_count != PROJECT_FILE_CHANGE_COUNT:
            self._reload()

        try:
            return self._settings[key]
        except KeyError:
            return app_settings.get(key, default)

    @classmethod
    def forget_window(cls, window_id: sublime.WindowId) -> None:
        cls._instances.pop(window_id, None)

    def _reload(self) -> None:
        project_data = self._window.project_data() or {}
        self._settings = project_data.get("settings", {}).get("GitSavvy", {}) or {}
        self._change_count = PROJECT_FILE_CHANGE_COUNT


class SettingsMixin:
    _project_settings: ProjectSettings | None = None

    @property
    def app_settings(self) -> _AppSettings:
        return app_settings

    @property
    def project_settings(self) -> ProjectSettings:
        if self._project_settings is None:
            self._project_settings = ProjectSettings(self.some_window())
        return self._project_settings

    def default_project_root(self) -> str:
        default_root = self.app_settings.get("users_home") or "~"
        if os.name == "nt" and default_root == "~":
            default_root = R"~\Desktop"
        return os.path.expanduser(default_root)

    def some_window(self) -> sublime.Window:
        return (
            maybe(lambda: self.window)  # type: ignore[attr-defined]
            or maybe(lambda: self.view.window())  # type: ignore[attr-defined]
            or sublime.active_window()
        )


PROJECT_FILE_CHANGE_COUNT = 0


class ProjectFileChanges(sublime_plugin.EventListener):
    def on_post_save(self, view: sublime.View) -> None:
        global PROJECT_FILE_CHANGE_COUNT
        file_path = view.file_name()
        if file_path and file_path.endswith(".sublime-project"):
            PROJECT_FILE_CHANGE_COUNT += 1

    def on_pre_close_window(self, window: sublime.Window) -> None:
        window_id = window.id()
        sublime.set_timeout(lambda: ProjectSettings.forget_window(window_id))


def color_value(namespace: str, key: str) -> str:
    colors = app_settings.get("colors", {})
    default_settings = read_default_settings()["colors"]
    try:
        return colors[namespace][key]
    except KeyError:
        return default_settings[namespace][key]


@lru_cache(maxsize=1)
def read_default_settings() -> dict[str, Any]:
    path = "Packages/GitSavvy/GitSavvy.sublime-settings"
    return sublime.decode_value(sublime.load_resource(path))
