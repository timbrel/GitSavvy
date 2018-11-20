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
    def setUp(self):
        original_window_id = sublime.active_window().id()
        sublime.run_command("new_window")

        yield lambda: sublime.active_window().id() != original_window_id

        self.window = sublime.active_window()
        self.view = sublime.active_window().new_file()
        # make sure we have a window to work with
        s = sublime.load_settings("Preferences.sublime-settings")
        s.set("close_windows_when_empty", False)

    def tearDown(self):
        if self.view:
            self.view.set_scratch(True)
            self.view.window().focus_view(self.view)
            self.view.close()
        self.window.run_command('close_window')
        unstub()

    def test_extract_clickable_filepaths_from_view(self):
        REPO_PATH = '/not/here'
        FILE_STATUS = dedent("""\
             M modified_file
            ?? new_file
            A  staged_file
            R  moved_file_new
            moved_file_old
            MM staged_and_unstaged_changes
        """.rstrip()).replace('\n', '\x00')
        STASH_LIST = dedent("""\
            stash@{0}: On fix-1055: /not/here/but_like_a_filename.py
            stash@{1}: On fix-1046: fix-1048
        """.rstrip())

        # We don't want to mock out *all* calls to 'os.path.exists' bc
        # that's usually risky, all things can depend on that.
        # We use `spy2` which will only record all invocations, followed
        # by a `when` to just fake the specific check for REPO_PATH.
        spy2('os.path.exists')
        when(os.path).exists(REPO_PATH).thenReturn(True)
        # Mocking `in_merge` is a bit surprising. TBC.
        when(StatusInterface).in_merge().thenReturn(False)
        when(StatusInterface).git('status', '--porcelain', '-z').thenReturn(FILE_STATUS)
        when(StatusInterface).git('status', '-b', '--porcelain').thenReturn('## the-branch')
        when(StatusInterface).git('log', ...).thenReturn('d9b34774 The last commit message')
        when(StatusInterface).git('stash', 'list').thenReturn(STASH_LIST)

        try:
            interface = StatusInterface(repo_path=REPO_PATH)

            view = interface.view
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

        finally:
            interface.view.close()
