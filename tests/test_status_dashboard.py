import os
from textwrap import dedent

import sublime

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import spy2, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core import store
from GitSavvy.common import ui
from GitSavvy.core.interfaces.status import StatusInterfaceCommand, GitCommand


class TestStatusDashboard(DeferrableTestCase):
    @classmethod
    def setUpClass(cls):
        # make sure we have a window to work with
        original_window_id = sublime.active_window().id()
        sublime.run_command("new_window")

        yield lambda: sublime.active_window().id() != original_window_id

        cls.window = sublime.active_window()

        s = sublime.load_settings("Preferences.sublime-settings")
        s.set("close_windows_when_empty", False)

    @classmethod
    def tearDownClass(cls):
        cls.window.run_command('close_window')

    def setUp(self):
        store.state.clear()
        self.create_new_view()

    def tearDown(self):
        unstub()

    def create_new_view(self, window=None):
        view = (window or sublime.active_window()).new_file()
        self.addCleanup(self.close_view, view)
        return view

    def close_view(self, view):
        if view:
            view.set_scratch(True)
            view.close()

    def create_status_interface(self, repo_path, file_status, last_commit, stash_list=''):
        # We don't want to mock out *all* calls to 'os.path.exists' bc
        # that's usually risky, all things can depend on that.
        # We use `spy2` which will only record all invocations, followed
        # by a `when` to just fake the specific check for REPO_PATH.
        spy2('os.path.exists')
        when(os.path).exists(repo_path).thenReturn(True)
        when(GitCommand).get_repo_path().thenReturn(repo_path)
        when(GitCommand).in_merge().thenReturn(False)
        when(GitCommand).in_cherry_pick().thenReturn(False)
        when(GitCommand).git('status', ...).thenReturn(file_status)
        when(GitCommand).git('log', ...).thenReturn(
            "{0}%00%00{1}".format(*last_commit.split(" ", 1)))
        when(GitCommand).git('stash', 'list').thenReturn(stash_list)
        when(GitCommand).git('for-each-ref', ...).thenReturn("")

        interface = ui.create_interface(self.window, repo_path, "status")
        view = interface.view

        self.addCleanup(lambda: view.close())
        return interface, view

    def test_extract_clickable_filepaths_from_view(self):
        REPO_PATH = '/not/here'
        FILE_STATUS = dedent("""\
            ## the-branch
             M modified_file
            ?? new_file
            A  staged_file
            R  moved_file_new
            moved_file_old
            MM staged_and_unstaged_changes
        """.rstrip()).replace('\n', '\x00')
        LAST_COMMIT = 'd9b34774 The last commit message'
        STASH_LIST = dedent("""\
            stash@{0}: On fix-1055: /not/here/but_like_a_filename.py
            stash@{1}: On fix-1046: fix-1048
        """.rstrip())

        interface, view = self.create_status_interface(
            REPO_PATH, FILE_STATUS, LAST_COMMIT, STASH_LIST
        )
        # The interface updates async.
        yield lambda: (
            view.find('fix-1048', 0, sublime.LITERAL)
            and view.find('modified_file', 0, sublime.LITERAL)
        )
        verify(GitCommand, atleast=1).git('status', ...)

        results = view.find_all_results()
        actual = [fpath for fpath, _, _ in results]
        expected = [
            '/not/here/modified_file',
            '/not/here/staged_and_unstaged_changes',
            '/not/here/new_file',
            '/not/here/staged_file',
            '/not/here/moved_file_new',
            '/not/here/staged_and_unstaged_changes',
        ]
        self.assertEqual(actual, expected)

    def test_clean_working_dir_has_no_clickable_elements(self):
        REPO_PATH = '/not/here'
        FILE_STATUS = dedent("""\
            ## the-branch
        """.rstrip()).replace('\n', '\x00')
        LAST_COMMIT = 'd9b34774 The last commit message'
        STASH_LIST = dedent("""\
            stash@{0}: On fix-1055: /not/here/but_like_a_filename.py
            stash@{1}: On fix-1046: fix-1048
        """.rstrip())

        interface, view = self.create_status_interface(
            REPO_PATH, FILE_STATUS, LAST_COMMIT, STASH_LIST
        )
        # The interface updates async.
        yield lambda: view.find('fix-1048', 0, sublime.LITERAL)

        actual = view.find_all_results()
        expected = []
        self.assertEqual(actual, expected)

    def await_std_interface(self):
        REPO_PATH = '/not/here'
        FILE_STATUS = dedent("""\
            ## the-branch
             M modified_file
            ?? new_file
            A  staged_file
            R  moved_file_new
            moved_file_old
            UU conflicting_file
        """.rstrip()).replace('\n', '\x00')
        LAST_COMMIT = 'd9b34774 The last commit message'
        STASH_LIST = dedent("""\
            stash@{0}: On fix-1055: /not/here/but_like_a_filename.py
            stash@{1}: On fix-1046: fix-1048
        """.rstrip())

        interface, view = self.create_status_interface(
            REPO_PATH, FILE_STATUS, LAST_COMMIT, STASH_LIST
        )
        # The interface updates async.
        yield lambda: (
            view.find('fix-1048', 0, sublime.LITERAL)
            and view.find('modified_file', 0, sublime.LITERAL)
        )

        region = sublime.Region(0, view.size())
        view.sel().clear()
        view.sel().add(region)

        window = view.window()
        when(sublime.View).window().thenReturn(window)
        when(window).status_message(...)
        when(interface).refresh_repo_status_and_render()

        return interface, view

    @p.expand([
        ('staged_file', 'staged', ['staged_file']),
        ('modified_file', 'unstaged', ['modified_file']),
        ('new_file', 'untracked', ['new_file']),
        ('moved_file_old', 'staged', ['moved_file_new']),
        ('conflicting_file', 'merge-conflicts', ['conflicting_file']),
        ('(0)', 'stashes', ['0'])
    ])
    def test_extract_subjects(self, SELECTED_FILE, SECTION, EXPECTED):
        interface, view = yield from self.await_std_interface()

        region = yield lambda: view.find(SELECTED_FILE, 0, sublime.LITERAL)
        view.sel().clear()
        view.sel().add(region.begin())
        self.assertEqual(StatusInterfaceCommand(view).get_selected_subjects(SECTION), EXPECTED)

    def test_stage_files(self):
        interface, view = yield from self.await_std_interface()
        when(GitCommand).stage_file(...)

        view.run_command('gs_status_stage_file')

        verify(GitCommand).stage_file('modified_file', 'new_file', 'conflicting_file', ...)
        verify(interface).refresh_repo_status_and_render()
        verify(view.window()).status_message("Staged files successfully.")
