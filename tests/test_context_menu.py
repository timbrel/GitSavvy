from __future__ import annotations

import os
import tempfile

from unittesting import DeferrableTestCase

from GitSavvy.core.commands import context_menu
from GitSavvy.tests.mockito import unstub, when


class TestContextMenuWatcher(DeferrableTestCase):
    def setUp(self) -> None:
        context_menu.stop_context_menu_watcher()

    def tearDown(self) -> None:
        context_menu.stop_context_menu_watcher()
        context_menu.reset_context()
        unstub()

    def test_file_commands_are_visible_only_with_a_file_path(self) -> None:
        command_types = (
            context_menu.gs_ctx_path_history,
            context_menu.gs_ctx_file_history,
            context_menu.gs_ctx_show_file_at_commit,
        )
        for file_path, expected in ((None, False), ("/repo/file.py", True)):
            context_menu.Context = Context(file_path)
            for command_type in command_types:
                self.assertEqual(command_type.is_visible(None), expected)

    def test_tracks_disable_context_menus(self) -> None:
        settings = WatchableSettings({"disable_context_menus": False})
        when(context_menu.sublime).load_settings(
            "GitSavvy.sublime-settings"
        ).thenReturn(settings)

        with tempfile.TemporaryDirectory() as cache_path:
            when(context_menu.sublime).cache_path().thenReturn(cache_path)
            menu_path = os.path.join(
                cache_path, "GitSavvy", "Context.sublime-menu"
            )

            context_menu.start_context_menu_watcher()

            with open(menu_path, encoding="utf-8") as file:
                self.assertEqual(
                    context_menu.sublime.decode_value(file.read()),
                    context_menu.CONTEXT_MENU
                )

            settings.set("font_size", 14)
            self.assertTrue(os.path.exists(menu_path))

            settings.set("disable_context_menus", True)
            self.assertFalse(os.path.exists(menu_path))

            settings.set("disable_context_menus", False)
            self.assertTrue(os.path.exists(menu_path))

            context_menu.stop_context_menu_watcher()
            settings.set("disable_context_menus", True)
            self.assertTrue(os.path.exists(menu_path))

    def test_removes_cached_menu_when_initially_disabled(self) -> None:
        settings = WatchableSettings({"disable_context_menus": True})
        when(context_menu.sublime).load_settings(
            "GitSavvy.sublime-settings"
        ).thenReturn(settings)

        with tempfile.TemporaryDirectory() as cache_path:
            when(context_menu.sublime).cache_path().thenReturn(cache_path)
            output_dir = os.path.join(cache_path, "GitSavvy")
            os.makedirs(output_dir)
            menu_path = os.path.join(output_dir, "Context.sublime-menu")
            with open(menu_path, "w", encoding="utf-8") as file:
                file.write("stale")

            context_menu.start_context_menu_watcher()

            self.assertFalse(os.path.exists(menu_path))


class Context:
    def __init__(self, file_path) -> None:
        self.file_path = file_path


class WatchableSettings(dict):
    def __init__(self, values: dict) -> None:
        super().__init__(values)
        self.callbacks = {}

    def add_on_change(self, key: str, callback) -> None:
        self.callbacks[key] = callback

    def clear_on_change(self, key: str) -> None:
        self.callbacks.pop(key, None)

    def set(self, key: str, value: object) -> None:
        self[key] = value
        for callback in list(self.callbacks.values()):
            callback()
