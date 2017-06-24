import os
import subprocess

from GitSavvy.core import git_command
from GitSavvy.tests.common import GitRepoTestCase
from GitSavvy.common.util import super_key


class CommonCommitMixin(object):
    """ Help methods for commit command testing """

    def get_view(self):
        """
        Get the currently active view, set it to self.view and
        return it's line_count
        """
        self.view = self.window.active_view()
        line_count = self.view.rowcol(self.view.size())[0]
        # print('Got view: %s, line count: %s' % (self.view, line_count))
        return line_count

    def assert_commit_view_rendered(self, first_line=None):
        """ Assert the commit view is correctly rendered """
        yield self.get_view  # yield back control when view has any lines
        first_rows = self.get_rows(*range(0, 2))
        self.assertEqual(first_rows, [first_line or '', ''])

        first_comment_row = self.get_row(2)
        self.assertEqual(first_comment_row, '## To make a commit, type your '
                         'commit message and press {key}-ENTER. To cancel'
                         .format(key=super_key))

    def finish_commit(self, message=None):
        """
        Execute the commit while inserting the
        given message in the start of the buffer
        """
        if message:
            self.view.run_command("insert", {"characters": message}),
        self.window.run_command('gs_commit_view_do_commit')
        yield lambda: not self.window.views()  # yield control when view closed

    def assert_commit_log_updated(self, message, count=2):
        """
        Assert commit was performed correctly by inspecting last commit
        message in log as well as the commit count
        """
        output = subprocess.check_output(["git", "log", "--oneline"],
                                         cwd=self._temp_dir)
        commits = output.splitlines()
        self.assertEqual(len(commits), 2)
        self.assertIn(message, commits[0].decode())


class TestCommit(CommonCommitMixin, GitRepoTestCase, git_command.GitCommand):

    def test_add_and_commit_file(self):
        """ Test adding a new file and committing """
        foo = os.path.join(self.repo_path, "foo")
        with open(foo, "w") as f:
            f.write("foo")

        self.stage_file(foo)
        self.window.run_command("gs_commit")

        yield from self.assert_commit_view_rendered(first_line=None)
        yield from self.finish_commit(message="Adding foo!")

        self.assert_commit_log_updated(message=' Adding foo!')


class TestAmend(CommonCommitMixin, GitRepoTestCase, git_command.GitCommand):

    def test_amend_commit(self):
        """ Test amending a recently added commit """
        foo = os.path.join(self.repo_path, "foo")
        with open(foo, "w") as f:
            f.write("foo")

        subprocess.check_call(["git", "add", "foo"], cwd=self._temp_dir)
        subprocess.check_call(["git", "commit", "-m", "Add foo"], cwd=self._temp_dir)
        self.assert_commit_log_updated(message=' Add foo')

        with open(foo, "w") as f:
            f.write("foo bar")

        self.stage_file(foo)
        self.window.run_command('gs_commit', {'amend': True})

        yield from self.assert_commit_view_rendered(first_line='Add foo')
        yield from self.finish_commit(message="Fix: ")

        # assert commit was performed correctly
        output = subprocess.check_output(["git", "log", "--oneline"],
                                         cwd=self._temp_dir)
        commits = output.splitlines()
        self.assertEqual(len(commits), 2)
        self.assertIn(' Fix: Add foo', commits[0].decode())


class TestAmendFirstCommit(CommonCommitMixin, GitRepoTestCase, git_command.GitCommand):
    initialize = False

    def test_amend_commit(self):
        """ Test amending the first commit """
        foo = os.path.join(self.repo_path, "foo")
        with open(foo, "w") as f:
            f.write("foo")

        subprocess.check_call(["git", "add", "foo"], cwd=self._temp_dir)
        subprocess.check_output(["git", "commit", "-m", "Add foo"], cwd=self._temp_dir)

        with open(foo, "w") as f:
            f.write("foo bar")

        self.stage_file(foo)
        self.window.run_command('gs_commit', {'amend': True})

        yield from self.assert_commit_view_rendered(first_line='Add foo')
        yield from self.finish_commit(message="Fix: ")

        # assert commit was performed correctly
        output = subprocess.check_output(["git", "log", "--oneline"],
                                         cwd=self._temp_dir)
        commits = output.splitlines()
        self.assertEqual(len(commits), 2)
        self.assertIn(' Fix: Add foo', commits[0].decode())
