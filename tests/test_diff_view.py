import os
import re

import sublime

from unittesting import DeferrableTestCase, AWAIT_WORKER
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p

import GitSavvy.core.commands.diff as module
from GitSavvy.core.commands.diff import gs_diff, gs_diff_refresh


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

    def test_compute_reference_document_updates_metadata_and_keeps_patch_origin(self):
        a = (
            "diff --git a/core/commands/multi_selector.py b/core/commands/multi_selector.py\n"
            "index 84df8d3f..8e9a6cb9 100644\n"
            "--- a/core/commands/multi_selector.py\n"
            "+++ b/core/commands/multi_selector.py\n"
            "@@ -23,7 +23,7 @@ __all__ = (\n"
            " Region: TypeAlias = \"list[int]\"\n"
            "\n"
            "\n"
            "-def get_selection(view: sublime.View) -> Iterable[sublime.Region]:\n"
            "+def  get_selection(view: sublime.View) -> Iterable[sublime.Region]:\n"
            "    multi_selection: list[Region] = view.settings().get(\"git_savvy.multi_selection\", [])\n"
            "    return (\n"
            "        sorted(starmap(sublime.Region, multi_selection))\n"
        )
        b = (
            "diff --git a/core/commands/multi_selector.py b/core/commands/multi_selector.py\n"
            "index 84df8d3f..75a1d3c4 100644\n"
            "--- a/core/commands/multi_selector.py\n"
            "+++ b/core/commands/multi_selector.py\n"
            "@@ -23,7 +23,7 @@ __all__ = (\n"
            " Region: TypeAlias = \"list[int]\"\n"
            "\n"
            "\n"
            "-def get_selection(view: sublime.View) -> Iterable[sublime.Region]:\n"
            "+def   get_selection(view: sublime.View) -> Iterable[sublime.Region]:\n"
            "    multi_selection: list[Region] = view.settings().get(\"git_savvy.multi_selection\", [])\n"
            "    return (\n"
            "        sorted(starmap(sublime.Region, multi_selection))\n"
        )
        expected = (
            "diff --git a/core/commands/multi_selector.py b/core/commands/multi_selector.py\n"
            "index 84df8d3f..75a1d3c4 100644\n"
            "--- a/core/commands/multi_selector.py\n"
            "+++ b/core/commands/multi_selector.py\n"
            "@@ -23,7 +23,7 @@ __all__ = (\n"
            " Region: TypeAlias = \"list[int]\"\n"
            "\n"
            "\n"
            "-def get_selection(view: sublime.View) -> Iterable[sublime.Region]:\n"
            "+def  get_selection(view: sublime.View) -> Iterable[sublime.Region]:\n"
            "    multi_selection: list[Region] = view.settings().get(\"git_savvy.multi_selection\", [])\n"
            "    return (\n"
            "        sorted(starmap(sublime.Region, multi_selection))\n"
        )

        actual = module.compute_reference_document(a, b)
        self.assertEqual(actual, expected)

    def test_compute_reference_document_drops_non_matching_changed_patch_lines(self):
        a = (
            "diff --git a/core/commands/multi_selector.py b/core/commands/multi_selector.py\n"
            "index 84df8d3f..8e9a6cb9 100644\n"
            "--- a/core/commands/multi_selector.py\n"
            "+++ b/core/commands/multi_selector.py\n"
            "@@ -23,7 +23,7 @@ __all__ = (\n"
            " Region: TypeAlias = \"list[int]\"\n"
            "\n"
            "\n"
            "-def get_selection(view: sublime.View) -> Iterable[sublime.Region]:\n"
            "+def  get_selection(view: sublime.View) -> Iterable[sublime.Region]:\n"
            "    multi_selection: list[Region] = view.settings().get(\"git_savvy.multi_selection\", [])\n"
            "    return (\n"
            "        sorted(starmap(sublime.Region, multi_selection))\n"
        )
        b = (
            "diff --git a/core/commands/multi_selector.py b/core/commands/multi_selector.py\n"
            "index 84df8d3f..f0642a05 100644\n"
            "--- a/core/commands/multi_selector.py\n"
            "+++ b/core/commands/multi_selector.py\n"
            "@@ -23,11 +23,11 @@ __all__ = (\n"
            " Region: TypeAlias = \"list[int]\"\n"
            "\n"
            "\n"
            "-def get_selection(view: sublime.View) -> Iterable[sublime.Region]:\n"
            "+def  get_selection(view: sublime.View) -> Iterable[sublime.Region]:\n"
            "    multi_selection: list[Region] = view.settings().get(\"git_savvy.multi_selection\", [])\n"
            "    return (\n"
            "        sorted(starmap(sublime.Region, multi_selection))\n"
            "-        if multi_selection\n"
            "+        if  multi_selection\n"
            "        else view.sel()\n"
            "    )\n"
        )
        expected = (
            "diff --git a/core/commands/multi_selector.py b/core/commands/multi_selector.py\n"
            "index 84df8d3f..f0642a05 100644\n"
            "--- a/core/commands/multi_selector.py\n"
            "+++ b/core/commands/multi_selector.py\n"
            "@@ -23,11 +23,11 @@ __all__ = (\n"
            " Region: TypeAlias = \"list[int]\"\n"
            "\n"
            "\n"
            "-def get_selection(view: sublime.View) -> Iterable[sublime.Region]:\n"
            "+def  get_selection(view: sublime.View) -> Iterable[sublime.Region]:\n"
            "    multi_selection: list[Region] = view.settings().get(\"git_savvy.multi_selection\", [])\n"
            "    return (\n"
            "        sorted(starmap(sublime.Region, multi_selection))\n"
            "        else view.sel()\n"
            "    )\n"
        )

        actual = module.compute_reference_document(a, b)
        self.assertEqual(actual, expected)

    def test_compute_reference_document_ignores_metadata_only_changes(self):
        a = (
            "diff --git a/foo.py b/foo.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -10,3 +10,3 @@ def run():\n"
            "-    before\n"
            "+    after\n"
            "     keep\n"
        )
        b = (
            "diff --git a/foo.py b/foo.py\n"
            "index 1111111..3333333 100644\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -20,3 +20,3 @@ def run():\n"
            "-    before\n"
            "+    after\n"
            "     keep\n"
        )

        actual = module.compute_reference_document(a, b)
        self.assertEqual(actual, b)

    @p.expand([
        ("@@ 1\n1234\n1567\n1890", (41, 46)),
        ("@@ 1\n1234\n1567", (41, 46)),
        ("@@ 1\n1567\n1890", (41, 46)),
        ("@@ 1\n1234", (41, 46)),
        ("@@ 1\n1567", (41, 46)),
        ("@@ 1\n1890", (41, 46)),

        ("@@ 1\n1XXX\n1XXX", (41, 46)),

        ("@@ X\n1234\n1567\n1890", (41, 46)),
        ("@@ X\n1567\n1890", (41, 46)),
        ("@@ X\n1234\n1567", (41, 46)),
        ("@@ X\n1234", (41, 46)),
        ("@@ X\n1567", (41, 46)),
        ("@@ X\n1890", (41, 46)),
        ("@@ X\n1XXX\n1567\n1890", (41, 46)),
        ("@@ X\n1234\n1567\n1XXX", (41, 46)),
        ("@@ X\n1XXX\n1567\n1XXX", (41, 46)),

        ("@@ X\n1234\n1XXX\n1XXX", None),
        ("@@ X\n1XXX\n1XXX\n1890", None),
        ("@@ X\n1XXX\n1XXX\n1XXX", None),
        ("@@ X\n0123", None),

        # Only consider first hunk in input
        ("@@ X\n1234\n1567\n1890\n@@ 2\n2345\n2678", (41, 46)),
        ("@@ X\n1234\n@@ 2\n2345\n2678", (41, 46)),
        ("@@ X\n1234\n1567\n1890\n@@ X\n2XXX\n2678", (41, 46)),

        # Ensure invalid input doesn't throw
        ("@@ X\n", None),
        ("1234\n1567\n1890", None),
    ])
    def test_find_hunk_in_view(self, IN, expected):
        VIEW_CONTENT = """\
0123
diff --git a/barz b/fooz
+++ b/fooz
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

        actual = module.find_hunk_in_view(view, "diff --git a/barz b/fooz\n+++ b/fooz\n" + IN)
        actual = (actual.a, actual.b) if actual else actual
        self.assertEqual(actual, expected)


class TestDiffViewJumpingToFile(DeferrableTestCase):
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

    @p.expand([
        (79, ('barz', 16, 1)),
        (80, ('barz', 16, 1)),
        (81, ('barz', 16, 2)),

        (85, ('barz', 17, 1)),
        (86, ('barz', 17, 2)),

        # on a '-' try to select next '+' line
        (111, ('barz', 20, 1)),  # jump to 'four'

        (209, ('boox', 17, 1)),  # jump to 'thr'
        (210, ('boox', 17, 2)),
        (211, ('boox', 17, 3)),
        (212, ('boox', 17, 4)),
        (213, ('boox', 17, 1)),
        (214, ('boox', 17, 1)),

        (223, ('boox', 19, 1)),  # all jump to 'sev'
        (228, ('boox', 19, 1)),
        (233, ('boox', 19, 1)),

        (272, ('boox', 25, 5)),
        (280, ('boox', 25, 5)),

        (319, ('boox', 30, 1)),  # but do not jump if indentation does not match

        # cursor on the hunk info line selects first diff line
        (58, ('barz', 16, 1)),
        (59, ('barz', 16, 1)),
        (89, ('barz', 20, 1)),
    ])
    def test_a(self, CURSOR, EXPECTED):
        VIEW_CONTENT = """\
prelude
--
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -16,1 +16,1 @@ Hi
 one
+two
@@ -20,1 +20,1 @@ Ho
-three
 context
+four
diff --git a/foxx b/boxx
--- a/foox
+++ b/boox
@@ -16,1 +16,1 @@ Hello
 one
-two
+thr
 fou
-fiv
-six
+sev
 eig
@@ -24 +24 @@ Hello
     one
-    two
     thr
@@ -30 +30 @@ Hello
     one
-    two
 thr
"""
        view = self.window.new_file()
        self.addCleanup(view.close)
        view.set_syntax_file("Packages/GitSavvy/syntax/diff_view.sublime-syntax")
        view.run_command('append', {'characters': VIEW_CONTENT})
        view.set_scratch(True)

        cmd = module.gs_diff_open_file_at_hunk(view)
        when(cmd).load_file_at_line(...)

        view.sel().clear()
        view.sel().add(CURSOR)

        cmd.run({'unused_edit'})

        # In all cases here "commit" is `None`
        verify(cmd).load_file_at_line(None, *EXPECTED)

    @p.expand([
        ("+++ b/barz", "barz"),
        # git puts a "\t" at EOL if the name has a space
        ("+++ b/Side Bar.sublime-menu\t", "Side Bar.sublime-menu"),
    ])
    def test_extracted_filename(self, B_LINE, EXPECTED):
        VIEW_CONTENT = """\
prelude
--
diff --git a/fooz b/barz
--- a/fooz
{b_line}
@@ -16,1 +16,1 @@ Hi
 one
+two
""".format(b_line=B_LINE)
        CURSOR = 79
        view = self.window.new_file()
        self.addCleanup(view.close)
        view.settings().set("translate_tabs_to_spaces", False)
        view.set_syntax_file("Packages/GitSavvy/syntax/diff_view.sublime-syntax")
        view.run_command('append', {'characters': VIEW_CONTENT})
        view.set_scratch(True)

        cmd = module.gs_diff_open_file_at_hunk(view)
        when(cmd).load_file_at_line(...)

        view.sel().clear()
        view.sel().add(CURSOR)

        cmd.run({'unused_edit'})

        # In all cases here "commit" is `None`
        verify(cmd).load_file_at_line(None, EXPECTED, ...)


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
        cmd = module.gs_diff_stage_or_reset_hunk(view)
        when(cmd).git(...)
        when(cmd.view).run_command("gs_clear_multiselect")
        when(cmd.view).run_command("gs_diff_refresh")
        when(cmd.view).run_command("gs_update_status")

        view.sel().clear()
        view.sel().add(CURSOR)

        cmd.run({'unused_edit'})

        history = view.settings().get('git_savvy.diff_view.history')
        self.assertEqual(len(history), 1)

        actual = history.pop()
        expected = [['apply', None, '--cached', '-'], HUNK, [CURSOR], IN_CACHED_MODE]
        self.assertEqual(actual, expected)

    HUNK3 = """\
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -16,1 +16,1 @@ Hi
 one
 two
@@ -20,1 +20,1 @@ Ho
 three
 four
"""

    HUNK4 = """\
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -20,1 +20,1 @@ Ho
 three
 four
diff --git a/foxx b/boxx
--- a/foox
+++ b/boox
@@ -16,1 +16,1 @@ Hello
 one
 two
"""

    @p.expand([
        # De-duplicate cursors in the same hunk
        ([58, 79], HUNK1),
        ([58, 79, 84], HUNK1),
        # Combine hunks
        ([58, 89], HUNK3),
        ([89, 170], HUNK4),

        # Ignore cursors not in a hunk
        ([2, 11, 58, 79], HUNK1),
        ([58, 89, 123], HUNK3),
        ([11, 89, 123, 170], HUNK4),
    ])
    def test_hunking_two_hunks(self, CURSORS, PATCH, IN_CACHED_MODE=False):
        VIEW_CONTENT = """\
prelude
--
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -16,1 +16,1 @@ Hi
 one
 two
@@ -20,1 +20,1 @@ Ho
 three
 four
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
        cmd = module.gs_diff_stage_or_reset_hunk(view)
        when(cmd).git(...)
        when(cmd.view).run_command("gs_clear_multiselect")
        when(cmd.view).run_command("gs_diff_refresh")
        when(cmd.view).run_command("gs_update_status")
        # when(module.gs_diff_stage_or_reset_hunk).git(...)
        # when(module).refresh(view)

        view.sel().clear()
        for c in CURSORS:
            view.sel().add(c)

        cmd.run({'unused_edit'})

        history = view.settings().get('git_savvy.diff_view.history')
        self.assertEqual(len(history), 1)

        actual = history.pop()
        expected = [['apply', None, '--cached', '-'], PATCH, CURSORS, IN_CACHED_MODE]
        self.assertEqual(actual, expected)

    def test_sets_unidiff_zero_if_no_contextual_lines(self):
        VIEW_CONTENT = """\
prelude
--
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -16,1 +16,1 @@ Hi
 one
 two
"""
        CURSOR = 58
        view = self.window.new_file()
        self.addCleanup(view.close)
        view.run_command('append', {'characters': VIEW_CONTENT})
        view.set_scratch(True)

        # view.settings().set('git_savvy.diff_view.in_cached_mode', IN_CACHED_MODE)
        view.settings().set('git_savvy.diff_view.history', [])
        view.settings().set('git_savvy.diff_view.context_lines', 0)

        cmd = module.gs_diff_stage_or_reset_hunk(view)
        when(cmd).git(...)
        when(cmd.view).run_command("gs_clear_multiselect")
        when(cmd.view).run_command("gs_diff_refresh")
        when(cmd.view).run_command("gs_update_status")

        view.sel().clear()
        view.sel().add(CURSOR)

        cmd.run({'unused_edit'})

        history = view.settings().get('git_savvy.diff_view.history')
        self.assertEqual(len(history), 1)

        actual = history.pop()[0]
        expected = ['apply', None, '--cached', '--unidiff-zero', '-']
        self.assertEqual(actual, expected)

    def test_unstaging_new_file_in_cached_mode_uses_reverse_apply(self):
        VIEW_CONTENT = """\
prelude
--
diff --git a/tests/new_file.py b/tests/new_file.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/tests/new_file.py
@@ -0,0 +1,2 @@
+one
+two
"""
        CURSOR = VIEW_CONTENT.index("+one")

        view = self.window.new_file()
        self.addCleanup(view.close)
        view.run_command('append', {'characters': VIEW_CONTENT})
        view.set_scratch(True)

        view.settings().set('git_savvy.diff_view.in_cached_mode', True)
        view.settings().set('git_savvy.diff_view.history', [])

        cmd = module.gs_diff_stage_or_reset_hunk(view)
        when(cmd).git(...)
        when(cmd.view).run_command("gs_clear_multiselect")
        when(cmd.view).run_command("gs_diff_refresh")
        when(cmd.view).run_command("gs_update_status")

        view.sel().clear()
        view.sel().add(CURSOR)

        cmd.run({'unused_edit'})

        history = view.settings().get('git_savvy.diff_view.history')
        self.assertEqual(len(history), 1)

        actual = history.pop()[0]
        expected = ['apply', '-R', '--cached', '-']
        self.assertEqual(actual, expected)

    def test_staging_new_file_in_uncached_mode_uses_add(self):
        VIEW_CONTENT = """\
prelude
--
diff --git a/tests/new_file.py b/tests/new_file.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/tests/new_file.py
@@ -0,0 +1,2 @@
+one
+two
"""
        CURSOR = VIEW_CONTENT.index("+one")

        view = self.window.new_file()
        self.addCleanup(view.close)
        view.run_command('append', {'characters': VIEW_CONTENT})
        view.set_scratch(True)

        view.settings().set('git_savvy.diff_view.in_cached_mode', False)
        view.settings().set('git_savvy.diff_view.history', [])

        cmd = module.gs_diff_stage_or_reset_hunk(view)
        when(cmd).git(...)
        when(cmd)._mark_untracked_files_as_staged(...)
        when(cmd.view).run_command("gs_clear_multiselect")
        when(cmd.view).run_command("gs_diff_refresh")
        when(cmd.view).run_command("gs_update_status")

        view.sel().clear()
        view.sel().add(CURSOR)

        cmd.run({'unused_edit'})

        history = view.settings().get('git_savvy.diff_view.history')
        self.assertEqual(len(history), 1)

        actual = history.pop()[0]
        expected = ["add", "--add-untracked-files", ["tests/new_file.py"]]
        self.assertEqual(actual, expected)

    def test_status_message_if_clean(self):
        VIEW_CONTENT = """\
prelude
--
"""
        view = self.window.new_file()
        self.addCleanup(view.close)
        view.run_command('append', {'characters': VIEW_CONTENT})
        view.set_scratch(True)

        window = mock()
        when(view).window().thenReturn(window)
        when(window).status_message(...)

        view.sel().clear()
        view.sel().add(0)

        # Manually instantiate the cmd so we can inject our known view
        cmd = module.gs_diff_stage_or_reset_hunk(view)
        cmd.run('_unused_edit')

        verify(window, times=1).status_message('The repo is clean.')

    def test_discard_does_not_run_when_target_file_is_dirty(self):
        VIEW_CONTENT = """\
prelude
--
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -16,1 +16,1 @@ Hi
 one
 two
"""
        CURSOR = 58
        view = self.window.new_file()
        self.addCleanup(view.close)
        view.run_command('append', {'characters': VIEW_CONTENT})
        view.set_scratch(True)

        view.settings().set('git_savvy.diff_view.in_cached_mode', False)
        view.settings().set('git_savvy.diff_view.history', [])

        cmd = module.gs_diff_stage_or_reset_hunk(view)
        when(cmd).discard_target_has_unsaved_view(...).thenReturn(True)
        when(cmd).git(...)

        view.sel().clear()
        view.sel().add(CURSOR)

        cmd.run({'unused_edit'}, reset=True)

        history = view.settings().get('git_savvy.diff_view.history')
        self.assertEqual(history, [])
        verify(cmd, times=0).git(...)


class TestZooming(DeferrableTestCase):
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
        (0, '--unified=0'),
        (1, '--unified=1'),
        (3, '--unified=3'),
        (5, '--unified=5'),
        (None, None)
    ])
    def test_adds_unified_flag_to_change_contextual_lines(self, CONTEXT_LINES, FLAG):
        view = self.window.new_file()
        self.addCleanup(view.close)
        view.set_scratch(True)
        view.settings().set("git_savvy.repo_path", "fake_repo_path")

        view.settings().set('git_savvy.diff_view.context_lines', CONTEXT_LINES)
        cmd = module.gs_diff_refresh(view)
        when(cmd).git(...).thenReturn(b'NEW CONTENT')

        cmd.run({'unused_edit'})
        verify(cmd).git('diff', None, FLAG, ...)

    def test_untracked_file_in_cached_mode_shows_staged_changes_header(self):
        repo_path = '/not/there'
        file_path = '/not/there/core/commands/test.py'

        view = self.window.new_file()
        self.addCleanup(view.close)
        view.set_scratch(True)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.settings().set("git_savvy.file_path", file_path)
        view.settings().set('git_savvy.diff_view.in_cached_mode', True)
        view.settings().set('git_savvy.diff_view.disable_stage', False)
        view.settings().set('git_savvy.diff_view.context_lines', 3)

        cmd = module.gs_diff_refresh(view)
        when(cmd).git(...).thenReturn(b'')
        when(cmd).is_probably_untracked_file(file_path).thenReturn(True)
        when(cmd).intent_to_add(file_path)
        when(cmd).undo_intent_to_add(file_path)

        cmd.run({'unused_edit'})

        buffer_content = view.substr(sublime.Region(0, view.size()))
        self.assertIn('(UNTRACKED)', buffer_content)
        self.assertIn('STAGED CHANGES (Will commit)', buffer_content)

    def test_untracked_folder_shows_folder_header(self):
        repo_path = '/not/there'
        folder_path = '/not/there/tests/packages/KeymapMenuCustomCommand/'

        view = self.window.new_file()
        self.addCleanup(view.close)
        view.set_scratch(True)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.settings().set("git_savvy.file_path", folder_path)
        view.settings().set('git_savvy.diff_view.in_cached_mode', False)
        view.settings().set('git_savvy.diff_view.disable_stage', False)
        view.settings().set('git_savvy.diff_view.context_lines', 3)

        cmd = module.gs_diff_refresh(view)
        when(cmd).git(...).thenReturn(b'')
        when(cmd).is_probably_untracked_file(folder_path).thenReturn(False)

        cmd.run({'unused_edit'})

        buffer_content = view.substr(sublime.Region(0, view.size()))
        expected_path = "tests{}packages{}KeymapMenuCustomCommand{}".format(
            os.sep,
            os.sep,
            os.sep,
        )
        self.assertIn(f'FOLDER: {expected_path}  (UNTRACKED)', buffer_content)
        self.assertNotIn('FILE: tests', buffer_content)

    @p.expand([
        (1, 5, 3),
        (2, 5, 3),
        (2, 5, 3),
        (3, 5, 5),
        (4, 5, 5),
        (5, 5, 10),
        (10, 5, 15),

        (15, -5, 10),
        (10, -5, 5),
        (5, -5, 3),
        (4, -5, 3),
        (3, -5, 1),
        (2, -5, 1),
        (1, -5, 1),
    ])
    def test_updates_view_state_when_zooming(self, BEFORE, AMOUNT, EXPECTED):
        view = self.window.new_file()
        self.addCleanup(view.close)
        view.set_scratch(True)

        view.settings().set('git_savvy.diff_view.context_lines', BEFORE)
        cmd = module.gs_diff_zoom(view)
        when(cmd.view).run_command("gs_diff_refresh")

        cmd.run({'unused_edit'}, AMOUNT)

        actual = view.settings().get('git_savvy.diff_view.context_lines')
        self.assertEqual(actual, EXPECTED)


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
        self.view.set_scratch(True)
        self.addCleanup(self.view.close)

    def tearDown(self):
        unstub()

    @p.expand([
        ('in_cached_mode', False),
        ('ignore_whitespace', False),
        ('base_commit', None),
        ('target_commit', None),
        ('show_diffstat', True),
        ('context_lines', 3),
        ('disable_stage', False),
        ('history', []),
        ('just_hunked', ''),
    ])
    def test_default_view_state(self, KEY, DEFAULT_VALUE):
        REPO_PATH = '/not/there'
        when(gs_diff_refresh).git('diff', ...).thenReturn('')
        cmd = gs_diff(self.window)
        when(cmd).get_repo_path().thenReturn(REPO_PATH)

        cmd.run()

        diff_view = self.window.active_view()
        self.addCleanup(diff_view.close)

        actual = diff_view.settings().get('git_savvy.diff_view.{}'.format(KEY))
        self.assertEqual(actual, DEFAULT_VALUE)

    def test_sets_repo_path(self):
        REPO_PATH = '/not/there'
        when(gs_diff_refresh).git('diff', ...).thenReturn('')
        cmd = gs_diff(self.window)
        when(cmd).get_repo_path().thenReturn(REPO_PATH)

        cmd.run()

        diff_view = self.window.active_view()
        self.addCleanup(diff_view.close)

        actual = diff_view.settings().get('git_savvy.repo_path')
        self.assertEqual(actual, REPO_PATH)

    def test_extract_clickable_lines(self):
        REPO_PATH = '/not/there'
        DIFF = fixture('diff_1.txt')

        when(gs_diff_refresh).git('diff', ...).thenReturn(DIFF.encode())
        cmd = gs_diff(self.window)
        when(cmd).get_repo_path().thenReturn(REPO_PATH)
        cmd.run()
        yield AWAIT_WORKER  # await activated_async
        yield AWAIT_WORKER  # await refresh async

        diff_view = self.window.active_view()
        self.addCleanup(diff_view.close)

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

        when(gs_diff_refresh).git('diff', ...).thenReturn(DIFF.encode())
        cmd = gs_diff(self.window)
        when(cmd).get_repo_path().thenReturn(REPO_PATH)
        cmd.run()
        yield AWAIT_WORKER  # await activated_async
        yield AWAIT_WORKER  # await refresh async

        diff_view = self.window.active_view()
        self.addCleanup(diff_view.close)

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

    def test_parse_diff(self):
        DIFF = fixture('diff_1.txt')
        diff = module.SplittedDiff.from_string(DIFF)
        self.assertEqual(diff.commit_for_hunk(diff.hunks[0]), None)

    def test_parse_commit(self):
        DIFF = fixture('diff_2.txt')
        diff = module.SplittedDiff.from_string(DIFF)

        self.assertEqual(len(diff.hunks), 2)
        self.assertEqual(len(diff.headers), 3)

        self.assertEqual(diff.head_for_hunk(diff.hunks[0]), diff.headers[-1])
        self.assertEqual(diff.head_for_hunk(diff.hunks[1]), diff.headers[-1])

        self.assertEqual(diff.commit_for_hunk(diff.hunks[0]), diff.commits[0])
        self.assertEqual(diff.commit_for_hunk(diff.hunks[1]), diff.commits[0])

        self.assertEqual(
            diff.commits[0].commit_hash(),
            "9dd4769f090aec1c6bceee49019680d0dba8108d"
        )
