import os
import re

import sublime

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import expect, unstub, when, spy2

from GitSavvy.core.commands.diff import GsDiffCommand, GsDiffRefreshCommand


THIS_DIRNAME = os.path.dirname(os.path.realpath(__file__))


def fixture(name):
    with open(os.path.join(THIS_DIRNAME, 'fixtures', name)) as f:
        return f.read()


class TestDiffView(DeferrableTestCase):
    def setUp(self):
        original_window_id = sublime.active_window().id()
        sublime.run_command("new_window")

        yield lambda: sublime.active_window().id() != original_window_id

        self.window = sublime.active_window()
        self.view = sublime.active_window().new_file()
        self.window.focus_view(self.view)
        yield lambda: sublime.active_window().active_view().id() == self.view.id()
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

    def test_extract_clickable_lines(self):
        REPO_PATH = '/not/there'
        FILE_PATH = '/not/there/README.md'
        DIFF = fixture('diff_1.txt')

        when(GsDiffRefreshCommand).git('diff', ...).thenReturn(DIFF)
        cmd = GsDiffCommand(self.window)
        when(cmd).get_repo_path().thenReturn(REPO_PATH)
        cmd.run_async()

        diff_view = self.window.active_view()
        actual = diff_view.find_all_results()
        # `find_all_results` only returns full filename-with-line matches.
        # These match clicking on `@@ -52,8 +XX,7` lines
        expected = [
            ('/not/there/core/commands/custom.py', 16, 0),
            ('/not/there/core/commands/diff.py', 52, 0),
            ('/not/there/core/commands/diff.py', 63, 0)
        ]

        self.assertEqual(actual, expected)

    def test_result_file_regex(self):
        REPO_PATH = '/not/there'
        FILE_PATH = '/not/there/README.md'
        DIFF = fixture('diff_1.txt')

        when(GsDiffRefreshCommand).git('diff', ...).thenReturn(DIFF)
        cmd = GsDiffCommand(self.window)
        when(cmd).get_repo_path().thenReturn(REPO_PATH)
        cmd.run_async()

        diff_view = self.window.active_view()
        BUFFER_CONTENT = diff_view.substr(sublime.Region(0, diff_view.size()))
        self.assertEqual(
            BUFFER_CONTENT,
            '''
  UNSTAGED CHANGES

--
''' + DIFF
        )

        regex = diff_view.settings().get('result_file_regex')

        matches = re.findall(regex, BUFFER_CONTENT, re.M)
        expected = [
            'core/commands/custom.py',
            'core/commands/diff.py',
            'core/commands/custom.py',
            'core/commands/custom.py',
            'core/commands/custom.py',
            'core/commands/diff.py',
            'core/commands/diff.py',
            'core/commands/diff.py'
        ]
        self.assertEqual(matches, expected)

        PRELUDE_HEIGHT = 4
        matches = re.finditer(regex, BUFFER_CONTENT, re.M)
        actual = [
            (m.group(0), diff_view.rowcol(m.span(1)[0])[0] + 1 - PRELUDE_HEIGHT)
            # Oh boy, a oneliner.          ^^^^^^^^^^^^ start offset
            #                      ^^^^^^ convert to (row, col)
            #                                          ^^^^^^^ only take row
            #                                          but add 1 for convenience
            for m in matches
        ]
        expected = [
            (' core/commands/custom.py |', 1),
            (' core/commands/diff.py   |', 2),
            ('diff --git a/core/commands/custom.py b/core/commands/custom.py', 5),
            ('--- a/core/commands/custom.py', 7),
            ('+++ b/core/commands/custom.py', 8),
            ('diff --git a/core/commands/diff.py b/core/commands/diff.py', 18),
            ('--- a/core/commands/diff.py', 20),
            ('+++ b/core/commands/diff.py', 21)
        ]
        self.assertEqual(actual, expected)
