import os
import subprocess
import tempfile
from unittest import TestCase

import sublime

from GitSavvy.common import util
from GitSavvy.core.commands import stash
from GitSavvy.core.commands.stash import GsStashSaveCommand
from GitSavvy.tests.mockito import unstub, verify, when
from GitSavvy.tests.parameterized import parameterized


class TestStashStaged(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(unstub)
        self.command = StashCommand(sublime.active_window())
        self.command.directory = self.directory.name
        self.command.stash_of_indexed = True
        self.command.include_untracked = False
        when(util.view).refresh_gitsavvy(...).thenReturn(None)
        self.git("init")
        self.git("config", "user.name", "Stash Test")
        self.git("config", "user.email", "stash@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.write_file("file", "base\n")
        self.write_file("project", "base\n")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.write_file("file", "older stash\n")
        self.git("stash", "push", "-m", "older stash")
        self.older_stash = self.git("rev-parse", "refs/stash")
        self.write_file("file", "current head\n")
        self.git("commit", "-am", "current")

    @parameterized.expand([("",), ("staged changes",)])
    def test_staged_only_does_not_apply_older_stash(self, description):
        self.write_file("project", "staged edit\n")
        self.git("add", "project")

        self.command.on_done(description)

        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("rev-parse", "stash@{1}"), self.older_stash)
        self.assertEqual(self.git("show", "stash@{0}:project"), "staged edit\n")
        self.assertEqual(len(self.git("stash", "list").splitlines()), 2)

    def test_nothing_staged_leaves_worktree_and_stashes_untouched(self):
        self.write_file("project", "unstaged edit\n")
        before = self.git("status", "--porcelain")

        with self.assertRaises(subprocess.CalledProcessError) as raised:
            self.command.on_done("")

        self.assertIn("No staged changes", raised.exception.output)
        self.assertEqual(self.git("status", "--porcelain"), before)
        self.assertEqual(self.git("diff", "--name-only"), "project\n")
        self.assertEqual(self.git("rev-parse", "refs/stash"), self.older_stash)
        self.assertEqual(self.git("ls-files", "--unmerged"), "")

    def test_unstaged_and_untracked_changes_are_preserved(self):
        self.write_file("project", "staged edit\n")
        self.git("add", "project")
        self.write_file("file", "unstaged edit\n")
        self.write_file("untracked", "untracked\n")

        self.command.on_done("")

        self.assertEqual(self.git("diff", "--cached"), "")
        self.assertEqual(self.git("diff", "--name-only"), "file\n")
        self.assertEqual(self.git("show", "stash@{0}:file"), "current head\n")
        self.assertEqual(self.git("rev-parse", "stash@{1}"), self.older_stash)
        with open(os.path.join(self.directory.name, "untracked")) as f:
            self.assertEqual(f.read(), "untracked\n")

    def test_overlapping_unstaged_edits_do_not_trigger_recovery_pop(self):
        self.write_file("project", "staged edit\n")
        self.git("add", "project")
        self.write_file("project", "overlapping unstaged edit\n")
        staged = self.git("diff", "--cached")
        unstaged = self.git("diff")

        with self.assertRaises(subprocess.CalledProcessError):
            self.command.on_done("")

        self.assertEqual(self.git("diff", "--cached"), staged)
        self.assertEqual(self.git("diff"), unstaged)
        self.assertEqual(self.git("ls-files", "--unmerged"), "")
        self.assertEqual(self.git("rev-parse", "stash@{1}"), self.older_stash)

    def test_old_git_does_not_modify_repository(self):
        self.command.git_version = (2, 34, 0)
        self.write_file("project", "staged edit\n")
        self.git("add", "project")
        before = self.git("status", "--porcelain")
        message = "Sorry, stashing staged changes requires Git 2.35 or newer."
        when(stash).show_noop_panel(self.command.window, message)

        self.command.on_done("")

        verify(stash).show_noop_panel(self.command.window, message)
        self.assertEqual(self.git("status", "--porcelain"), before)
        self.assertEqual(self.git("rev-parse", "refs/stash"), self.older_stash)

    def git(self, *args):
        return self.command.git(*args)

    def write_file(self, name, content):
        with open(os.path.join(self.directory.name, name), "w") as f:
            f.write(content)


class StashCommand(GsStashSaveCommand):
    # Pin both dates so the old two-stash implementation deterministically
    # creates identical commits for staged-only changes and an empty message.
    git_version = (2, 35, 0)

    def git(self, *args, **kwargs):
        env = dict(os.environ, GIT_AUTHOR_DATE="2026-10-27T11:25:34+0100",
                   GIT_COMMITTER_DATE="2026-10-27T11:25:34+0100")
        return subprocess.run(
            ["git"] + [arg for arg in args if arg is not None],
            cwd=self.directory, env=env, input=kwargs.get("stdin"),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True
        ).stdout
