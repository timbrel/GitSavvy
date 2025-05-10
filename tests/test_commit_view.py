import os
from textwrap import dedent

import sublime

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.commands.commit import extract_commit_message, gs_prepare_commit_refresh_diff


examples = [
    (
        dedent("""\

        """.rstrip()),
        ""
    ),
    (
        dedent("""\

        ## To make a commit, ...
        """.rstrip()),
        ""
    ),
    (
        dedent("""\
        The subject
        ## To make a commit, ...
        """.rstrip()),
        "The subject"
    ),
    (
        dedent("""\
        The subject
        b
        c
        d
        ## To make a commit, ...
        """.rstrip()),
        dedent("""\
        The subject
        b
        c
        d
        """.rstrip())
    ),

]


class TestExtractCommitMessage(DeferrableTestCase):
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

    @p.expand(examples)
    def test_a(self, VIEW_CONTENT, output):
        view = self.window.new_file()
        self.addCleanup(view.close)

        view.set_syntax_file("Packages/GitSavvy/syntax/make_commit.sublime-syntax")
        view.run_command('append', {'characters': VIEW_CONTENT})
        view.set_scratch(True)

        self.assertEqual(output, extract_commit_message(view).strip())

    def test_basic_default_commit_view(self):
        view = self.window.new_file()
        self.addCleanup(view.close)
        exists = os.path.exists
        when(os.path).exists(...).thenAnswer(exists)
        when(os.path).exists("/foo").thenReturn(True)
        when(gs_prepare_commit_refresh_diff).git("diff", ...).thenReturn(dedent("""\
        diff --git a/bar/test.txt b/bar/test.txt
        index 9303f2c..5a9ce64 100644
        --- a/bar/test.txt
        +++ b/bar/test.txt
        @@ -1,14 +1,22 @@
        This is a diff
        """.rstrip()).encode())

        self.window.run_command("gs_commit", {"repo_path": "/foo"})
        yield self.window.active_view().name() == "COMMIT: foo"

        commit_view = self.window.active_view()
        self.assertTrue(commit_view.find_by_selector("meta.dropped.git.commit"))
        self.assertTrue(commit_view.find_by_selector("git-savvy.diff"))

        self.assertEqual("", extract_commit_message(commit_view).rstrip())
