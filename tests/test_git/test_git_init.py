import os
from .common import GitRepoTestCase
from GitSavvy.core import git_command


class TestInitialization(GitRepoTestCase, git_command.GitCommand):

    def test_is_master(self):
        branch = self.get_current_branch_name()
        self.assertEqual(branch, "master")


class TestStageFile(GitRepoTestCase, git_command.GitCommand):

    def test_stage_file(self):
        foo = os.path.join(self.repo_path, "foo")
        with open(foo, "w") as f:
            f.write("foo")
        self.assert_git_status([0, 0, 1, 0])
        self.stage_file(foo)
        self.assert_git_status([1, 0, 0, 0])
