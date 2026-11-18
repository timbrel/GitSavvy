from __future__ import annotations

import sublime

from unittesting import DeferrableTestCase
from GitSavvy.tests.parameterized import parameterized as p
from GitSavvy.tests.mockito import mock, unstub, verify, when

from GitSavvy.core.interfaces import log_graph_panels as panels
from GitSavvy.core.interfaces.log_graph_panels import gs_log_graph_edit_files
from GitSavvy.core.interfaces.log_graph_renderer import gs_log_graph_refresh


class TestGraphFileListing(DeferrableTestCase):
    def setUp(self) -> None:
        self.command = gs_log_graph_edit_files(mock())
        self.addCleanup(unstub)
        self.addCleanup(self.command.list_controlled_files.cache_clear)

    @p.expand([
        ("", []),
        ("normal.md\0", ["normal.md"]),
        (' spaced \0tab\tname\0line\nname\0quote".md\0ü.md\0',
         [" spaced ", "tab\tname", "line\nname", 'quote".md', "ü.md"])
    ])
    def test_preserves_filenames(self, output: str, expected: list[str]) -> None:
        when(self.command).git("ls-tree", ...).thenReturn(output)

        self.assertEqual(self.command.list_controlled_files(0), expected)
        verify(self.command).git("ls-tree", "-r", "-z", "--full-tree", "--name-only", "HEAD")

    def test_caches_until_graph_changes(self) -> None:
        when(self.command).git("ls-tree", ...).thenReturn("first\0", "second\0")

        self.assertEqual(self.command.list_controlled_files(0), ["first"])
        self.assertEqual(self.command.list_controlled_files(0), ["first"])
        self.assertEqual(self.command.list_controlled_files(1), ["second"])
        verify(self.command, times=2).git("ls-tree", ...)


class TestGraphFileInput(DeferrableTestCase):
    def setUp(self) -> None:
        settings_view = sublime.active_window().new_file()
        settings_view.set_scratch(True)
        self.addCleanup(settings_view.close)
        self.settings = settings_view.settings()
        self.window = mock()
        self.view = mock()
        when(self.view).settings().thenReturn(self.settings)
        when(self.view).window().thenReturn(self.window)
        when(self.view).change_count().thenReturn(0)
        self.command = gs_log_graph_edit_files(self.view)
        self.addCleanup(unstub)
        when(self.command).list_controlled_files(0).thenReturn(["README.md", "src/main.py"])
        when(self.view).run_command(...).thenReturn(None)
        when(self.window).run_command(...).thenReturn(None)
        when(panels).enqueue_on_ui(...).thenAnswer(lambda fn, *args: fn(*args))

    def test_opens_the_input_palette(self) -> None:
        self.command.run(None, selected_index=2)

        verify(self.window).run_command("show_overlay", {
            "overlay": "command_palette",
            "command": "gs_log_graph_edit_files",
            "args": {"selected_index": 2}
        })
        self.assertIsNone(self.command.input({"path": "README.md"}))

    def test_lists_selected_paths_first_then_files_and_directories(self) -> None:
        self.set_paths(["src", "deleted.py"])
        handler = self.command.input({"selected_index": 1})
        items, index = handler.list_items()

        self.assertEqual([item.value for item in items], [
            "src", "deleted.py", "README.md", "src/main.py"
        ])
        self.assertEqual([item.text for item in items], [
            "✓ src", "✓ deleted.py", "  README.md", "  src/main.py"
        ])
        self.assertEqual([item.annotation for item in items], ["", "", "", ""])
        self.assertEqual(index, 1)
        self.assertEqual(handler.name(), "path")

    @p.expand([(True, []), (False, ["src"])])
    def test_no_indentation_without_active_selected_paths(self, active: bool, paths: list[str]) -> None:
        self.set_paths(paths, active=active)
        handler = self.command.input({})
        items, _ = handler.list_items()

        self.assertEqual([item.text for item in items], ["README.md", "src", "src/main.py"])
        self.assertEqual([item.value for item in items], ["README.md", "src", "src/main.py"])

    @p.expand([(True, "unselect"), (False, "select")])
    def test_preview_describes_the_highlighted_item(self, active: bool, action: str) -> None:
        self.set_paths(["src"], active=active)
        handler = self.handler()

        self.assertEqual(
            handler.preview("src"), f"[ctrl+enter] to {action}, [enter] to also close"
        )
        self.assertEqual(
            handler.preview("README.md"), "[ctrl+enter] to select, [enter] to also close"
        )

    def test_empty_list_cannot_be_confirmed(self) -> None:
        when(self.command).list_controlled_files(0).thenReturn([])
        handler = self.command.input({})
        items, _ = handler.list_items()

        self.assertEqual(items[0].text, "There are no files available.")
        self.assertFalse(handler.validate(items[0].value))
        self.assertFalse(handler.validate("missing"))

    @p.expand([("src", []), ("README.md", ["src", "README.md"])])
    def test_enter_toggles_and_closes(self, path: str, expected: list[str]) -> None:
        self.set_paths(["src"])
        handler = self.handler()
        self.assertTrue(handler.want_event())
        self.assertTrue(handler.validate(path))
        handler.confirm(path, {})
        self.command.run(None, path=path)

        self.assertEqual(self.settings.get("git_savvy.log_graph_view.paths"), expected)
        verify(self.view).run_command("gs_log_graph_refresh", {
            "assume_complete_redraw": bool(expected)
        })
        verify(panels, times=0).enqueue_on_ui(...)

    @p.expand([
        ("src", "ctrl", 0),
        ("src/main.py", "ctrl", 1),
        ("README.md", "primary", 1)
    ])
    def test_ctrl_enter_toggles_and_reopens(
        self, path: str, modifier: str, selected_index: int
    ) -> None:
        self.set_paths(["src"])
        handler = self.handler()
        handler.confirm(path, {"modifier_keys": {modifier: True}})
        self.command.run(None, path=path)

        self.assertEqual(
            path in self.settings.get("git_savvy.log_graph_view.paths"), path != "src"
        )
        verify(self.view).run_command("gs_log_graph_edit_files", {"selected_index": selected_index})
        self.assertIsNone(self.command._input_handler)

    def test_selecting_with_disabled_filters_starts_fresh(self) -> None:
        self.set_paths(["src"], active=False)
        self.settings.set("git_savvy.log_graph_view.filters", "--author=someone")
        handler = self.handler()
        handler.confirm("src", {})
        self.command.run(None, path="src")

        self.assertEqual(self.settings.get("git_savvy.log_graph_view.paths"), ["src"])
        self.assertTrue(self.settings.get("git_savvy.log_graph_view.apply_filters"))
        self.assertEqual(self.settings.get("git_savvy.log_graph_view.filters"), "")

    def test_selecting_preserves_active_filters(self) -> None:
        self.set_paths([])
        self.settings.set("git_savvy.log_graph_view.filters", "--author=someone")
        handler = self.handler()
        handler.confirm("src", {})
        self.command.run(None, path="src")

        self.assertEqual(self.settings.get("git_savvy.log_graph_view.filters"), "--author=someone")

    def test_cancel_does_not_change_paths_or_leak_modifier_state(self) -> None:
        self.set_paths(["src"])
        handler = self.handler()
        handler.confirm("src", {"modifier_keys": {"ctrl": True}})
        handler.cancel()

        self.assertEqual(self.settings.get("git_savvy.log_graph_view.paths"), ["src"])
        self.command.run(None, path="README.md")
        verify(self.window).run_command("hide_overlay")
        verify(panels, times=1).enqueue_on_ui(...)

    def handler(self):
        handler = self.command.input({})
        handler.list_items()
        return handler

    def set_paths(self, paths: list[str], active: bool = True) -> None:
        self.settings.set("git_savvy.log_graph_view.paths", paths)
        self.settings.set("git_savvy.log_graph_view.apply_filters", active)


class TestGraphFileInputPalette(DeferrableTestCase):
    def setUp(self) -> None:
        self.window = sublime.active_window()
        self.view = self.window.new_file()
        self.view.set_scratch(True)
        self.addCleanup(self.view.close)
        self.addCleanup(unstub)
        self.addCleanup(self.window.run_command, "hide_overlay")
        when(gs_log_graph_edit_files).list_controlled_files(...).thenReturn(["README.md", "src/main.py"])
        when(gs_log_graph_refresh).run(...).thenReturn(None)
        self.view.settings().set("git_savvy.log_graph_view", True)
        self.previews = []
        when(panels.GraphFilesInputHandler).preview(...).thenAnswer(
            lambda value: self.previews.append(value) or "Preview"
        )

    def test_palette_invokes_handler_and_applies_selection(self):
        self.view.run_command("gs_log_graph_edit_files", {"selected_index": 0})
        yield lambda: bool(self.previews)
        self.assertEqual(self.previews[-1], "README.md")

        self.window.run_command("select")
        yield lambda: self.view.settings().get("git_savvy.log_graph_view.paths") == ["README.md"]
