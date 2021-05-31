import sublime
import subprocess
import os
from unittesting.helpers import TempDirectoryTestCase


def tidy_path(path):
    return os.path.realpath(os.path.normcase(path))


startupinfo = None
if os.name == "nt":
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW


class AssertionsMixin:

    def assert_git_status(self, expected_status):
        """
        Assertion of the current git status. `status` should be a list of 4 intergers:
        The lengths of the staged, unstaged, untracked and conflicted entries.
        """
        status = self.get_working_dir_status()
        self.assertEqual(
            [
                len(status.staged_files),
                len(status.unstaged_files),
                len(status.untracked_files),
                len(status.merge_conflicts)
            ],
            expected_status)


class GitRepoTestCase(TempDirectoryTestCase, AssertionsMixin):
    """
    TempDirectoryTestCase is a subclass of DeferrableTestCase which creates and opens a temp
    directory before running the test case and close the window when the test case finishes running.
    https://github.com/randy3k/UnitTesting/blob/master/unittesting/helpers.py
    """
    # add readme file as the initial commit
    initialize = True

    @classmethod
    def setUpClass(cls):
        # since TempDirectoryTestCase.setUpClass is a generator
        yield from super(GitRepoTestCase, cls).setUpClass()
        assert tidy_path(cls._temp_dir) == tidy_path(sublime.active_window().folders()[0])
        assert tidy_path(cls._temp_dir) == tidy_path(cls.window.folders()[0])
        subprocess.check_call(
            ["git", "init"], cwd=cls._temp_dir, startupinfo=startupinfo
        )
        if cls.initialize:
            cls.add_readme()

    @classmethod
    def add_readme(cls):
        readme = os.path.join(cls._temp_dir, "README.md")
        f = open(readme, "w")
        f.write("README")
        f.close()
        subprocess.check_call(
            ["git", "add", "README.md"], cwd=cls._temp_dir, startupinfo=startupinfo
        )
        subprocess.check_call(
            ["git", "commit", "-m", "Add README.md"], cwd=cls._temp_dir, startupinfo=startupinfo
        )
