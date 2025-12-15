import sublime
from typing import Final

from mockito import unstub, when
from unittesting import DeferrableTestCase
from GitSavvy.core.git_command import GitCommand
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.commands.log_graph_renderer import gs_log_graph_refresh


class _TestBase(DeferrableTestCase):
    @classmethod
    def setUpClass(cls):
        s = sublime.load_settings("Preferences.sublime-settings")
        s.set("close_windows_when_empty", False)

        sublime.run_command("new_window")
        cls.window: Final[sublime.Window]
        cls.window = window = sublime.active_window()
        cls.addClassCleanup(lambda: window.run_command('close_window'))

    def tearDown(self):
        unstub()

    def await_string_in_view(self, view, needle):
        yield lambda: view.find(needle, 0, sublime.LITERAL)

    def await_active_panel_to_be(self, name):
        yield lambda: self.window.active_panel() == name

    def create_new_window(self):
        sublime.run_command("new_window")
        window = sublime.active_window()
        self.addCleanup(lambda: window.run_command('close_window'))
        return window

    def create_new_view(self, window=None):
        if window is None:
            window = self.window
        view = window.new_file()
        self.addCleanup(self.close_view, view)
        return view

    def close_view(self, view):
        view.set_scratch(True)
        view.close()


class TestRebaseActions(_TestBase):
    @p.expand([
        ("main", "", "main@{1}"),
        ("main", "main@{1}", "main@{2}"),
        ("main", "main@{1} main@{3}", "main@{1} main@{4}"),
        ("main", "main@{3} main@{1}", "main@{3} main@{2}"),
        ("main", "main@{3} main@{2}", "main@{3} main@{3}"),
        ("main", "main@{3} main@{3}", "main@{3} main@{4}"),
    ])
    def test_add_previous_tip(self, branch_name, input, expected):
        when(GitCommand).git("rev-parse", "--short", ...).thenReturn("deadbee")
        when(gs_log_graph_refresh).run(...).thenReturn()

        view = self.create_new_view()
        view.settings().set("git_savvy.log_graph_view.apply_filters", True)
        view.settings().set("git_savvy.log_graph_view.filters", input)

        view.run_command("gs_log_graph_add_previous_tip", {"branch_name": branch_name})

        actual = view.settings().get("git_savvy.log_graph_view.filters")
        self.assertEqual(expected, actual)

    @p.expand([
        ("main", "main@{2}", "main@{1}"),
        ("main", "main@{1}", ""),
        ("main", "main@{1} main@{3}", "main@{1} main@{2}"),
        ("main", "main@{1} main@{2}", "main@{1} main@{1}"),
        ("main", "main@{1} main@{1}", "main@{1}"),
    ])
    def test_remove_previous_tip(self, branch_name, input, expected):
        when(GitCommand).git("rev-parse", "--short", ...).thenReturn("deadbee")
        when(gs_log_graph_refresh).run(...).thenReturn()

        view = self.create_new_view()
        view.settings().set("git_savvy.log_graph_view.apply_filters", True)
        view.settings().set("git_savvy.log_graph_view.filters", input)

        view.run_command("gs_log_graph_remove_previous_tip", {"branch_name": branch_name})

        actual = view.settings().get("git_savvy.log_graph_view.filters")
        self.assertEqual(expected, actual)
