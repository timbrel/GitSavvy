import os

import sublime

from unittesting import DeferrableTestCase, expectedFailure
from GitSavvy.tests.parameterized import parameterized as p
from GitSavvy.tests.mockito import mock, spy2, unstub, verify, when

from GitSavvy.core import app_state, store
from GitSavvy.core.interfaces import log_graph
from GitSavvy.core.interfaces.log_graph import (
    extract_commit_hash,
    gs_log_graph_edit_files,
    navigate_to_symbol,
    GitCommand
)
from GitSavvy.core.interfaces.log_graph_renderer import gs_log_graph_refresh
from GitSavvy.core.interfaces.show_commit_info import gs_show_commit_info
from GitSavvy.core.git_mixins.status import WorkingDirState
from GitSavvy.core.settings import app_settings


RUNNING_ON_LINUX = os.environ.get('RUNNER_OS') == 'Linux'
expectedFailureOnGithubLinux = expectedFailure if RUNNING_ON_LINUX else lambda f: f


if os.name == 'nt':
    # On Windows, `find_all_results` returns pseudo linux paths
    # E.g. `/C/not/here/README.md`
    def cleanup_fpath(fpath):
        return fpath[2:]
else:
    def cleanup_fpath(fpath):
        return fpath


THIS_DIRNAME = os.path.dirname(os.path.realpath(__file__))
COMMIT_1 = 'This is commit fec0aca'
COMMIT_2 = 'This is commit f461ea1'
CLEAN_WORKING_DIR = WorkingDirState([], [], [], [])


def fixture(name):
    with open(os.path.join(THIS_DIRNAME, 'fixtures', name)) as f:
        return f.read()


EXTRACT_COMMIT_HASH_FIXTURE = r"""
* 1948764 (HEAD -> master) f
| *-.   3fe5938 (master_merge) Merge branches 'one' and 'two' into master_merge
| |\ \
|/ / /
| | * 0a8f459 (two) ccc
| | * 2084353 c
| | * 006bcdd c
* | | f8ceeb0 d
| |/
|/|
| | *   9b42732 (refs/stash) On one: a
| | |\
| |/ /
| | * c12cffd index on one: c3bba58 bb
| |/
| * c3bba58 (one) bb
| * 18fd299 aa
|/
* fe67af3 b
* 5e42cd1 a
● | |   3c2e064 (tag: 2.17.4) Merge pull request #983 from divmain/release/2.17.4          (6 months ago) <Randy Lai>
|\ \ \
| ● \ \   b0d95ed Merge pull request #988 from divmain/help_text               (6 months ago) <Simon>
| |\ \ \
| | ● | | f950461 Fix: help_text will be stripped                              (6 months ago) <Randy Lai>
| | | ● 6df205d (fork/diff-view-refreshes, diff-view-refreshes) Expect failures on Linux Travis
""".strip().split('\n')
HASHES = r"""
1948764
3fe5938


0a8f459
2084353
006bcdd
f8ceeb0


9b42732


c12cffd

c3bba58
18fd299

fe67af3
5e42cd1
3c2e064

b0d95ed

f950461
6df205d
""".strip().split('\n')


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
        when(log_graph).enqueue_on_ui(...).thenAnswer(lambda fn, *args: fn(*args))

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
        verify(log_graph, times=0).enqueue_on_ui(...)

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
        verify(log_graph, times=1).enqueue_on_ui(...)

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
        when(log_graph.GraphFilesInputHandler).preview(...).thenAnswer(
            lambda value: self.previews.append(value) or "Preview"
        )

    def test_palette_invokes_handler_and_applies_selection(self):
        self.view.run_command("gs_log_graph_edit_files", {"selected_index": 0})
        yield lambda: bool(self.previews)
        self.assertEqual(self.previews[-1], "README.md")

        self.window.run_command("select")
        yield lambda: self.view.settings().get("git_savvy.log_graph_view.paths") == ["README.md"]


class TestGraphViewCommitHashExtraction(DeferrableTestCase):
    @p.expand(list(zip(EXTRACT_COMMIT_HASH_FIXTURE, HASHES)))
    def test_extract_commit_hash_from_line(self, line, expected):
        actual = extract_commit_hash(line)
        self.assertEqual(actual, expected)


class TestGraphViewInteractionWithCommitInfoPanel(DeferrableTestCase):
    @classmethod
    def setUpClass(cls):
        s = sublime.load_settings("Preferences.sublime-settings")
        s.set("close_windows_when_empty", False)

    def setUp(self):
        self.window = window = self.create_new_window()
        self.create_new_view(window)

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

    def create_new_view(self, window):
        view = window.new_file()
        self.addCleanup(self.close_view, view)
        return view

    def close_view(self, view):
        view.set_scratch(True)
        view.close()

    def set_app_setting(self, key, value):
        original_value = app_settings.get(key)
        app_settings.set(key, value)
        self.addCleanup(app_settings.set, key, original_value)

    def set_app_state(self, key, value, default):
        original_value = app_state.get(key, default)
        app_state.set(key, value)
        self.addCleanup(app_state.set, key, original_value)

    def register_commit_info(self, info):
        for sha1, info in info.items():
            when(gs_show_commit_info).read_commit(sha1, ...).thenReturn(info)
            when(gs_show_commit_info).to_short_hash(sha1).thenReturn(sha1)

    def create_graph_view_async(self, repo_path, log, wait_for, markers=None):
        when(gs_log_graph_refresh).read_graph(...).thenReturn(
            line.replace("|", "%00") for line in log.splitlines(keepends=True)
        )
        # `GitCommand.get_repo_path` "validates" a given repo using
        # `os.path.exists`.
        spy2('os.path.exists')
        when(os.path).exists(repo_path).thenReturn(True)
        when(GitCommand).get_working_dir_status().thenReturn(CLEAN_WORKING_DIR)
        when(gs_log_graph_refresh).get_worktrees().thenRaise(AssertionError("Unexpected Git query"))
        when(gs_log_graph_refresh).get_worktree_locations().thenReturn([])
        if markers is not None:
            when(gs_log_graph_refresh).get_worktree_markers().thenReturn(markers)
        self.window.run_command('gs_graph', {'repo_path': repo_path})
        yield lambda: self.window.active_view().settings().get('git_savvy.log_graph_view') is True
        log_view = self.window.active_view()
        yield from self.await_string_in_view(log_view, wait_for)

        return log_view

    def setup_graph_view_async(self, show_commit_info_setting=True):
        REPO_PATH = '/not/there'
        LOG = fixture('log_graph_1.txt')

        self.set_app_state(
            'graph_show_more_commit_info',
            show_commit_info_setting,
            default=True
        )
        self.set_app_setting('git_status_in_status_bar', False)
        self.register_commit_info({
            'fec0aca': COMMIT_1,
            'f461ea1': COMMIT_2
        })

        log_view = yield from self.create_graph_view_async(
            REPO_PATH, LOG, wait_for='57b00b1'
        )
        if show_commit_info_setting:
            yield from self.await_active_panel_to_be('output.show_commit_info')

        return log_view

    def test_graph_without_linked_worktrees_avoids_query(self):
        yield from self.setup_graph_view_async(show_commit_info_setting=False)
        verify(gs_log_graph_refresh, times=0).get_worktrees()

    def test_graph_marks_other_worktrees(self):
        self.set_app_state('graph_show_more_commit_info', False, default=True)
        self.set_app_setting('git_status_in_status_bar', False)
        log = (
            "* fec0aca|HEAD -> main, feature, bookmark|Subject|Today, Author|parent\n"
            "* abcd123||Unrelated commit|Today, Author|parent\n"
            "* f461ea1|bookmark2|Subject|Today, Author|parent\n"
            "* 57b00b1||Subject|Today, Author|parent\n"
        )
        markers = ({"feature"}, {"f461ea1" + "0" * 33, "57b00b1" + "0" * 33})
        view = yield from self.create_graph_view_async(
            "/not/there", log, wait_for="57b00b1", markers=markers
        )
        text = view.substr(sublime.Region(0, view.size()))
        self.assertIn("(HEAD -> main, feature ■, bookmark)", text)
        self.assertIn("(bookmark2, ■)", text)
        self.assertIn("57b00b1 (■)", text)
        self.assertIn("abcd123 Unrelated commit", text)
        verify(gs_log_graph_refresh, times=0).get_worktrees()

    def test_multiple_detached_worktrees_each_get_a_marker(self):
        self.set_app_state('graph_show_more_commit_info', False, default=True)
        self.set_app_setting('git_status_in_status_bar', False)
        log = (
            "* fec0aca|HEAD -> main|Current|Today, Author|parent\n"
            "* f461ea1||Detached|Today, Author|\n"
        )
        view = yield from self.create_graph_view_async(
            "/not/there", log, wait_for="f461ea1",
            markers=(set(), ["f461ea1" + "0" * 33] * 2)
        )
        self.assertIn("f461ea1 (■, ■)", view.substr(sublime.Region(0, view.size())))

    @p.expand(["123456", "123456789abc", "123456789abc" + "0" * 28])
    def test_detached_markers_use_the_displayed_hash_length(self, short_hash):
        self.set_app_state('graph_show_more_commit_info', False, default=True)
        self.set_app_setting('git_status_in_status_bar', False)
        log = (
            "* fec0aca|HEAD -> main|Current|Today, Author|parent\n"
            "* {}||Detached|Today, Author|\n".format(short_hash)
        )
        view = yield from self.create_graph_view_async(
            "/not/there", log, wait_for=short_hash,
            markers=(set(), {"123456789abc" + "0" * 28})
        )
        text = view.substr(sublime.Region(0, view.size()))
        self.assertIn("{} (■)".format(short_hash), text)
        self.assertIn("⌂", text)

    def test_detached_markers_support_mixed_hash_lengths(self):
        self.set_app_state('graph_show_more_commit_info', False, default=True)
        self.set_app_setting('git_status_in_status_bar', False)
        log = (
            "* fec0aca|HEAD -> main|Current|Today, Author|parent\n"
            "* 1234567||Detached short|Today, Author|parent\n"
            "* abcdef123456||Detached long|Today, Author|parent\n"
            "* 1234568||Unrelated|Today, Author|parent\n"
            "* 8765432||Detached short again|Today, Author|parent\n"
        )
        view = yield from self.create_graph_view_async(
            "/not/there", log, wait_for="8765432",
            markers=(set(), {"1234567" + "0" * 33, "abcdef123456" + "0" * 28, "8765432" + "0" * 33})
        )
        text = view.substr(sublime.Region(0, view.size()))
        for commit in ("1234567", "abcdef123456", "8765432"):
            self.assertIn("{} (■)".format(commit), text)
        self.assertIn("1234568 Unrelated", text)

    @p.expand([(0, False, 0), (8, False, 8), (8, True, 10), (12, True, 12)])
    def test_graph_learns_lengths_when_extracting_hashes(self, cached, wide_graph, expected):
        self.set_app_state('graph_show_more_commit_info', False, default=True)
        self.set_app_setting('git_status_in_status_bar', False)
        repo_path = '/not/there'
        state = store.current_state(repo_path)
        previous = state.get('short_hash_length')
        self.addCleanup(lambda: (
            state.pop('short_hash_length', None) if previous is None
            else state.update({'short_hash_length': previous})
        ))
        state['short_hash_length'] = cached
        graph_art = '/ ' * 24 if wide_graph else ''
        log = (
            "* fec0aca|HEAD -> main|Current|Today, Author|parent\n"
            f"{graph_art}* abcdef1234||Longer hash|Today, Author|parent\n"
            "* 57b00b1||Shorter again|Today, Author|parent\n"
        )
        yield from self.create_graph_view_async(repo_path, log, wait_for='57b00b1')
        self.assertEqual(expected, state['short_hash_length'])

    def test_hidden_info_panel_after_create(self):
        yield from self.setup_graph_view_async(show_commit_info_setting=False)

        actual = self.window.active_panel()
        expected = None
        self.assertEqual(actual, expected)

    def test_if_the_user_issues_our_toggle_command_open_the_panel(self):
        yield from self.setup_graph_view_async(show_commit_info_setting=False)

        self.window.run_command('gs_log_graph_toggle_commit_info_panel')
        yield from self.await_active_panel_to_be('output.show_commit_info')

        actual = self.window.active_panel()
        expected = 'output.show_commit_info'
        self.assertEqual(actual, expected)

    def test_open_info_panel_after_create(self):
        yield from self.setup_graph_view_async()

        actual = self.window.active_panel()
        expected = 'output.show_commit_info'
        self.assertEqual(actual, expected)

    def test_info_panel_shows_first_commit_initially(self):
        yield from self.setup_graph_view_async()
        panel = self.window.find_output_panel('show_commit_info')

        yield from self.await_string_in_view(panel, COMMIT_1)

    def test_info_panel_shows_second_commit_after_navigate(self):
        log_view = yield from self.setup_graph_view_async()
        panel = self.window.find_output_panel('show_commit_info')

        log_view.run_command('gs_log_graph_navigate')
        yield from self.await_string_in_view(panel, COMMIT_2)

    def test_info_panel_shows_second_commit_after_cursor_moves(self):
        log_view = yield from self.setup_graph_view_async()
        panel = self.window.find_output_panel('show_commit_info')

        navigate_to_symbol(log_view, 'f461ea1')
        yield from self.await_string_in_view(panel, COMMIT_2)

    def test_if_the_user_issues_our_toggle_command_close_the_panel_and_keep_it(self):
        log_view = yield from self.setup_graph_view_async()

        self.window.run_command('gs_log_graph_toggle_commit_info_panel')
        actual = self.window.active_panel()
        expected = None
        self.assertEqual(actual, expected)

        # Ensure it doesn't open on navigate
        log_view.run_command('gs_log_graph_navigate')

        yield 500  # ?

        actual = self.window.active_panel()
        expected = None
        self.assertEqual(actual, expected)

    def test_if_the_user_opens_another_panel_dont_fight(self):
        window = self.window
        log_view = yield from self.setup_graph_view_async()
        active_group = window.active_group()

        window.run_command('show_panel', {'panel': 'console'})
        # We need both to get the cursor back
        window.focus_group(active_group)
        window.focus_view(log_view)
        log_view.run_command('gs_log_graph_navigate')

        yield 500  # ?

        actual = self.window.active_panel()
        expected = 'console'
        self.assertEqual(actual, expected)

    def test_if_the_user_closes_the_panel_accept_it(self):
        log_view = yield from self.setup_graph_view_async()

        self.window.run_command('hide_panel')
        log_view.run_command('gs_log_graph_navigate')

        yield 500  # ?

        actual = self.window.active_panel()
        expected = None
        self.assertEqual(actual, expected)

    def test_show_correct_info_if_user_moves_around_and_then_toggles_panel(self):
        log_view = yield from self.setup_graph_view_async()
        panel = self.window.find_output_panel('show_commit_info')
        # close panel
        self.window.run_command('gs_log_graph_toggle_commit_info_panel')

        # move around
        navigate_to_symbol(log_view, 'f461ea1')

        # show panel
        self.window.run_command('gs_log_graph_toggle_commit_info_panel')

        yield from self.await_string_in_view(panel, COMMIT_2)

    def test_show_correct_info_if_user_moves_around_and_then_opens_panel(self):
        log_view = yield from self.setup_graph_view_async()
        panel = self.window.find_output_panel('show_commit_info')
        # close panel
        self.window.run_command('gs_log_graph_toggle_commit_info_panel')

        # move around
        navigate_to_symbol(log_view, 'f461ea1')

        # show panel e.g. via mouse
        self.window.run_command('show_panel', {'panel': 'output.show_commit_info'})

        yield from self.await_string_in_view(panel, COMMIT_2)

    @expectedFailureOnGithubLinux
    def test_auto_close_panel_if_user_moves_away(self):
        view = self.create_new_view(self.window)
        yield from self.setup_graph_view_async()

        self.window.focus_view(view)

        self.assertTrue(self.window.active_panel() is None)

    def test_auto_show_panel_if_log_view_gains_focus_again(self):
        view = self.create_new_view(self.window)
        log_view = yield from self.setup_graph_view_async()

        self.window.focus_view(view)
        self.window.focus_view(log_view)

        self.assertEqual(self.window.active_panel(), 'output.show_commit_info')

    def test_do_not_hide_panel_if_it_gains_focus(self):
        yield from self.setup_graph_view_async()
        panel = self.window.find_output_panel('show_commit_info')

        self.window.focus_view(panel)

        self.assertEqual(self.window.active_panel(), 'output.show_commit_info')
