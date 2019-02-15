import os
import re

import sublime

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import when, unstub
from GitSavvy.tests.parameterized import parameterized as p

import GitSavvy.core.commands.diff as module
from GitSavvy.core.commands.diff import GsDiffCommand, GsDiffRefreshCommand


THIS_DIRNAME = os.path.dirname(os.path.realpath(__file__))


def fixture(name):
    with open(os.path.join(THIS_DIRNAME, 'fixtures', name)) as f:
        return f.read()


class TestDiffViewInternalFunctions(DeferrableTestCase):
    @classmethod
    def setUpClass(cls):
        sublime.run_command("new_window")
        cls.window = sublime.active_window()
        s = sublime.load_settings("Preferences.sublime-settings")
        s.set("close_windows_when_empty", False)

    @classmethod
    def tearDownClass(self):
        self.window.run_command('close_window')

    @p.expand([
        ([1, 2, 3, 4, 5], [[1, 2, 3, 4, 5], [2, 3, 4], [3]]),
        ([1, 2, 3, 4], [[1, 2, 3, 4], [2, 3]]),
        ([1, 2, 3], [[1, 2, 3], [2]]),
        ([1, 2], [[1, 2]]),
        ([1], [[1]]),
        ([], [])
    ])
    def test_shrink_list(self, IN, expected):
        actual = module.shrink_list_sym(IN)
        actual = list(actual)
        self.assertEqual(actual, expected)

    @p.expand([
        (26, [(20, 24), (15, 19), (10, 14), (5, 9), (0, 4)]),
        (25, [(20, 24), (15, 19), (10, 14), (5, 9), (0, 4)]),

        (24, [(15, 19), (10, 14), (5, 9), (0, 4)]),
        (23, [(15, 19), (10, 14), (5, 9), (0, 4)]),
        (22, [(15, 19), (10, 14), (5, 9), (0, 4)]),
        (21, [(15, 19), (10, 14), (5, 9), (0, 4)]),
        (20, [(15, 19), (10, 14), (5, 9), (0, 4)]),

        (19, [(10, 14), (5, 9), (0, 4)]),
        (18, [(10, 14), (5, 9), (0, 4)]),
        (17, [(10, 14), (5, 9), (0, 4)]),
        (16, [(10, 14), (5, 9), (0, 4)]),
        (15, [(10, 14), (5, 9), (0, 4)]),

        (14, [(5, 9), (0, 4)]),
        (13, [(5, 9), (0, 4)]),
        (12, [(5, 9), (0, 4)]),
        (11, [(5, 9), (0, 4)]),
        (10, [(5, 9), (0, 4)]),

        (9, [(0, 4)]),
        (8, [(0, 4)]),
        (7, [(0, 4)]),
        (6, [(0, 4)]),
        (5, [(0, 4)]),

        (4, []),
        (3, []),
        (2, []),
        (1, []),
        (0, []),

        (-1, []),
    ])
    def test_line_ranges_before_point(self, IN, expected):
        # ATT: All '0123' actually are '0123\n' (length: 5)
        VIEW_CONTENT = """\
0123
0123
0123
0123
0123
"""
        view = self.window.new_file()
        self.addCleanup(view.close)
        view.run_command('append', {'characters': VIEW_CONTENT})
        view.set_scratch(True)

        actual = module.line_regions_before_pt(view, IN)
        # unpack `sublime.Region`s
        actual = module.pickle_sel(actual)

        self.assertEqual(actual, expected)

    @p.expand([
        (0, None),
        (1, None),
        (2, None),
        (3, None),
        (4, None),

        (10, (5, 9)),
        (11, (5, 9)),
        (12, (5, 9)),
        (13, (5, 9)),
        (14, (5, 9)),
        (15, (5, 9)),
        (16, (5, 9)),
        (17, (5, 9)),
        (18, (5, 9)),
        (19, (5, 9)),

        (25, (20, 24)),
        (26, (20, 24)),
        (27, (20, 24)),
        (28, (20, 24)),
        (29, (20, 24)),
        (30, (20, 24)),
        (31, (20, 24)),
        (32, (20, 24)),
        (33, (20, 24)),
        (34, (20, 24)),
        (35, (20, 24)),

    ])
    def test_first_hunk_start_before_pt(self, IN, expected):
        VIEW_CONTENT = """\
0123
@@ 1
1123
1123
@@ 2
2123
2123
"""

        view = self.window.new_file()
        self.addCleanup(view.close)
        view.run_command('append', {'characters': VIEW_CONTENT})
        view.set_scratch(True)

        actual = module.first_hunk_start_before_pt(view, IN)
        actual = (actual.a, actual.b) if actual else actual
        self.assertEqual(actual, expected)

    @p.expand([
        ("@@ 1\n1234\n1567\n1890", (30, 34)),
        ("@@ 1\n1234\n1567", (30, 34)),
        ("@@ 1\n1567\n1890", (30, 34)),
        ("@@ 1\n1234", (30, 34)),
        ("@@ 1\n1567", (30, 34)),
        ("@@ 1\n1890", (30, 34)),

        ("@@ 1\n1XXX\n1XXX", (30, 34)),

        ("@@ X\n1234\n1567\n1890", (30, 34)),
        ("@@ X\n1567\n1890", (30, 34)),
        ("@@ X\n1234\n1567", (30, 34)),
        ("@@ X\n1234", (30, 34)),
        ("@@ X\n1567", (30, 34)),
        ("@@ X\n1890", (30, 34)),
        ("@@ X\n1XXX\n1567\n1890", (30, 34)),
        ("@@ X\n1234\n1567\n1XXX", (30, 34)),
        ("@@ X\n1XXX\n1567\n1XXX", (30, 34)),

        ("@@ X\n1234\n1XXX\n1XXX", None),
        ("@@ X\n1XXX\n1XXX\n1890", None),
        ("@@ X\n1XXX\n1XXX\n1XXX", None),
        ("@@ X\n0123", None),

        # Only consider first hunk in input
        ("@@ X\n1234\n1567\n1890\n@@ 2\n2345\n2678", (30, 34)),
        ("@@ X\n1234\n@@ 2\n2345\n2678", (30, 34)),
        ("@@ X\n1234\n1567\n1890\n@@ X\n2XXX\n2678", (30, 34)),

        # Ensure invalid input doesn't throw
        ("@@ X", None),
        ("@@ X\n", None),
        ("1234\n1567\n1890", None),
    ])
    def test_find_hunk_in_view(self, IN, expected):
        VIEW_CONTENT = """\
0123
diff --git a/barz b/fooz
@@ 1
1234
1567
1890
@@ 2
2345
2678
"""

        view = self.window.new_file()
        self.addCleanup(view.close)
        view.run_command('append', {'characters': VIEW_CONTENT})
        view.set_scratch(True)

        actual = module.find_hunk_in_view(view, IN)
        actual = (actual.a, actual.b) if actual else actual
        self.assertEqual(actual, expected)


class TestDiffViewHunking(DeferrableTestCase):
    @classmethod
    def setUpClass(cls):
        sublime.run_command("new_window")
        cls.window = sublime.active_window()
        s = sublime.load_settings("Preferences.sublime-settings")
        s.set("close_windows_when_empty", False)

    @classmethod
    def tearDownClass(self):
        self.window.run_command('close_window')

    def tearDown(self):
        unstub()

    HUNK1 = """\
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -16,1 +16,1 @@ Hi
 one
 two
"""
    HUNK2 = """\
diff --git a/foxx b/boxx
--- a/foox
+++ b/boox
@@ -16,1 +16,1 @@ Hello
 one
 two
"""

    @p.expand([
        (58, HUNK1),
        (68, HUNK1),
        (79, HUNK1),
        (84, HUNK1),
        (88, HUNK1),
        (136, HUNK2),
        (146, HUNK2),
        (156, HUNK2),
        (166, HUNK2),
        (169, HUNK2),
        (170, HUNK2),  # at EOF should work
    ])
    def test_hunking_one_hunk(self, CURSOR, HUNK, IN_CACHED_MODE=False):
        # Docstring here to get verbose parameterized printing
        """"""
        VIEW_CONTENT = """\
prelude
--
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -16,1 +16,1 @@ Hi
 one
 two
diff --git a/foxx b/boxx
--- a/foox
+++ b/boox
@@ -16,1 +16,1 @@ Hello
 one
 two
"""
        view = self.window.new_file()
        self.addCleanup(view.close)
        view.run_command('append', {'characters': VIEW_CONTENT})
        view.set_scratch(True)

        view.settings().set('git_savvy.diff_view.in_cached_mode', IN_CACHED_MODE)
        view.settings().set('git_savvy.diff_view.history', [])
        cmd = module.GsDiffStageOrResetHunkCommand(view)
        when(cmd).git(...)
        when(cmd.view).run_command("gs_diff_refresh")

        view.sel().clear()
        view.sel().add(CURSOR)

        cmd.run({'unused_edit'})
        yield 'AWAIT_WORKER'
        yield 'AWAIT_WORKER'

        history = view.settings().get('git_savvy.diff_view.history')
        self.assertEqual(len(history), 1)

        actual = history.pop()
        expected = [['apply', None, '--cached', '-'], HUNK, CURSOR, IN_CACHED_MODE]
        self.assertEqual(actual, expected)


class TestDiffView(DeferrableTestCase):
    @classmethod
    def setUpClass(cls):
        sublime.run_command("new_window")
        cls.window = sublime.active_window()
        s = sublime.load_settings("Preferences.sublime-settings")
        s.set("close_windows_when_empty", False)

    @classmethod
    def tearDownClass(self):
        self.window.run_command('close_window')

    def setUp(self):
        self.view = self.window.new_file()

    def tearDown(self):
        if self.view:
            self.view.set_scratch(True)
            self.view.close()

        unstub()

    def test_extract_clickable_lines(self):
        REPO_PATH = '/not/there'
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
