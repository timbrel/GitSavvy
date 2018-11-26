import os
from textwrap import dedent

import sublime

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import unstub, when, spy2

from GitSavvy.core.interfaces.status import StatusInterface


if os.name == 'nt':
    # On Windows, `find_all_results` returns pseudo linux paths
    # E.g. `/C/not/here/README.md`
    def cleanup_fpath(fpath):
        return fpath[2:]
else:
    def cleanup_fpath(fpath):
        return fpath


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
        self.create_new_view()

    def tearDown(self):
        self.do_cleanup()
        unstub()

    # `addCleanup` doesn't work either in Sublime at all or
    # with the DeferrableTestCase so we do a quick implementation
    # here.
    def add_cleanup(self, fn, *args, **kwargs):
        self._cleanups.append((fn, args, kwargs))

    def do_cleanup(self):
        while self._cleanups:
            fn, args, kwrags = self._cleanups.pop()
            try:
                fn(*args, **kwrags)
            except Exception:
                pass

    def create_new_view(self, window=None):
        view = (window or sublime.active_window()).new_file()
        self.add_cleanup(self.close_view, view)
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
        # Mocking `in_merge` is a bit surprising. TBC.
        when(StatusInterface).in_merge().thenReturn(False)
        when(StatusInterface).git('status', ...).thenReturn(file_status)
        when(StatusInterface).git('log', ...).thenReturn(last_commit)
        when(StatusInterface).git('stash', 'list').thenReturn(stash_list)

        interface = StatusInterface(repo_path=repo_path)
        view = interface.view

        self.add_cleanup(lambda: view.close())
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
        yield lambda: view.find('fix-1048', 0, sublime.LITERAL)

        results = view.find_all_results()
        actual = [cleanup_fpath(fpath) for fpath, _, _ in results]
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
