import os

import sublime

from unittesting import DeferrableTestCase, expectedFailure
from GitSavvy.tests.parameterized import parameterized as p
from GitSavvy.tests.mockito import spy2, unstub, when

from GitSavvy.core.commands.log_graph import (
    gs_log_graph_refresh,
    extract_commit_hash,
    navigate_to_symbol,
    GitCommand
)
from GitSavvy.core.commands.show_commit_info import gs_show_commit_info
from GitSavvy.core.git_mixins.status import WorkingDirState
from GitSavvy.core.settings import GitSavvySettings


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

    def set_global_setting(self, key, value):
        settings = GitSavvySettings()
        original_value = settings.get(key)
        settings.set(key, value)
        self.addCleanup(settings.set, key, original_value)

    def register_commit_info(self, info):
        for sha1, info in info.items():
            when(gs_show_commit_info).read_commit(sha1, ...).thenReturn(info)
            when(gs_show_commit_info).get_short_hash(sha1).thenReturn(sha1)

    def create_graph_view_async(self, repo_path, log, wait_for):
        when(gs_log_graph_refresh).read_graph(...).thenReturn(
            line.replace("|", "%00") for line in log.splitlines(keepends=True)
        )
        # `GitCommand.get_repo_path` "validates" a given repo using
        # `os.path.exists`.
        spy2('os.path.exists')
        when(os.path).exists(repo_path).thenReturn(True)
        when(GitCommand).get_working_dir_status().thenReturn(CLEAN_WORKING_DIR)
        self.window.run_command('gs_graph', {'repo_path': repo_path})
        yield lambda: self.window.active_view().settings().get('git_savvy.log_graph_view') is True
        log_view = self.window.active_view()
        yield from self.await_string_in_view(log_view, wait_for)

        return log_view

    def setup_graph_view_async(self, show_commit_info_setting=True):
        REPO_PATH = '/not/there'
        LOG = fixture('log_graph_1.txt')

        self.set_global_setting('graph_show_more_commit_info', show_commit_info_setting)
        self.set_global_setting('git_status_in_status_bar', False)
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
