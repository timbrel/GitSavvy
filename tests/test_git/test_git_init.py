import sublime
import os
import subprocess
from unittesting.helpers import TempDirectoryTestCase
from .common import AssertionsMixin
from GitSavvy.core import git_command


def tidy_path(path):
    return os.path.realpath(os.path.normcase(path))


class TestInitialization(TempDirectoryTestCase, AssertionsMixin, git_command.GitCommand):
    """
    TempDirectoryTestCase is a subclass of DeferrableTestCase which creates and opens a temp
    directory before running the test case and close the window when the test case finishes running.
    https://github.com/randy3k/UnitTesting/blob/master/unittesting/helpers.py
    """

    def test_01_init(self):
        self.assertEqual(
            tidy_path(self._temp_dir),
            tidy_path(sublime.active_window().folders()[0]))
        subprocess.check_call(["git", "init"], cwd=self._temp_dir)
        self.assertEqual(
            tidy_path(self._temp_dir),
            tidy_path(self.window.folders()[0]))

    def test_02_add_first_file(self):
        readme = os.path.join(self.repo_path, "README.md")
        f = open(readme, "w")
        f.write("README")
        f.close()
        self.git("add", "README.md")
        self.git("commit", "-m", "Init")

    def test_03_is_master(self):
        branch = self.get_current_branch_name()
        self.assertEqual(branch, "master")

    def test_04_untrack_file(self):
        foo = os.path.join(self.repo_path, "foo")
        with open(foo, "w") as f:
            f.write("foo")
        self.assert_git_status([0, 0, 1, 0])
        self.stage_file(foo)
        self.assert_git_status([1, 0, 0, 0])
