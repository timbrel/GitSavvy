import shutil
import subprocess
import sys
import tempfile

import sublime

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.parameterized import parameterized as p, param

from GitSavvy.core.git_command import GitCommand
from GitSavvy.core import git_mixins


class TestGitMixinsUsage(DeferrableTestCase):
    def tearDown(self):
        unstub()


class TestFetchInterface(TestGitMixinsUsage):
    def test_fetch_all(self):
        repo = GitCommand()
        when(repo).git("fetch", "--prune", "--all", None)
        repo.fetch()

    def test_fetch_remote(self):
        repo = GitCommand()
        when(repo).git("fetch", "--prune", "origin", None)
        repo.fetch("origin")

    def test_fetch_branch(self):
        repo = GitCommand()
        when(repo).git("fetch", "--prune", "origin", "master")
        repo.fetch("origin", "master")

    def test_fetch_remote_local_mapping(self):
        repo = GitCommand()
        when(repo).git("fetch", "--prune", "origin", "moster:muster")
        repo.fetch("origin", remote_branch="moster", local_branch="muster")
        repo.fetch(remote="origin", remote_branch="moster", local_branch="muster")

    @p.expand([
        (param(refspec="monster:manster"),),
        (param(None, "master"),),
        (param(remote_branch="master"),),
        (param(local_branch="master"),),

        (param("origin", "mi:mu", remote_branch="master"),),
        (param("origin", "mi:mu", local_branch="master"),),

    ])
    def test_invalid_calls(self, parameters):
        repo = GitCommand()
        when(repo).git("fetch", "--prune", ...)
        self.assertRaises(TypeError, lambda: repo.fetch(*parameters.args, **parameters.kwargs))


sha_and_subject = "\x0089b79cd737465ed308ecc00289d00a6f923f2da5\x00The Subject"


class TestGetBranchesParsing(TestGitMixinsUsage):
    def test_local_branch(self):
        repo = GitCommand()
        git_output = " \x00refs/heads/master\x00refs/remotes/origin/master\x00" + sha_and_subject
        when(repo).git("for-each-ref", ...).thenReturn(git_output)
        actual = list(repo.get_branches())
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "master",
                None,
                "master",
                "89b79cd737465ed308ecc00289d00a6f923f2da5",
                "The Subject",
                "origin/master",
                "",
                False,
                "",
                git_mixins.branches.Upstream(
                    "origin", "master", "origin/master", ""
                )
            )
        ])

    def test_active_local_branch(self):
        repo = GitCommand()
        git_output = "*\x00refs/heads/master\x00refs/remotes/origin/master\x00" + sha_and_subject
        when(repo).git("for-each-ref", ...).thenReturn(git_output)
        actual = list(repo.get_branches())
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "master",
                None,
                "master",
                "89b79cd737465ed308ecc00289d00a6f923f2da5",
                "The Subject",
                "origin/master",
                "",
                True,
                "",
                git_mixins.branches.Upstream(
                    "origin", "master", "origin/master", ""
                )
            )
        ])

    def test_remote_branch(self):
        repo = GitCommand()
        git_output = " \x00refs/remotes/origin/dev\x00\x00" + sha_and_subject
        when(repo).git("for-each-ref", ...).thenReturn(git_output)
        actual = list(repo.get_branches())
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "dev",
                "origin",
                "origin/dev",
                "89b79cd737465ed308ecc00289d00a6f923f2da5",
                "The Subject",
                "",
                "",
                False,
                "",
                None
            )
        ])

    def test_tracking_status(self):
        repo = GitCommand()
        git_output = " \x00refs/heads/master\x00refs/remotes/origin/master\x00gone" + sha_and_subject
        when(repo).git("for-each-ref", ...).thenReturn(git_output)
        actual = list(repo.get_branches())
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "master",
                None,
                "master",
                "89b79cd737465ed308ecc00289d00a6f923f2da5",
                "The Subject",
                "origin/master",
                "gone",
                False,
                "",
                git_mixins.branches.Upstream(
                    "origin", "master", "origin/master", "gone"
                )
            )
        ])

    def test_tracking_local_branch(self):
        repo = GitCommand()
        git_output = " \x00refs/heads/test\x00refs/heads/update-branch-from-upstream\x00" + sha_and_subject
        when(repo).git("for-each-ref", ...).thenReturn(git_output)
        actual = list(repo.get_branches())
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "test",
                None,
                "test",
                "89b79cd737465ed308ecc00289d00a6f923f2da5",
                "The Subject",
                "./update-branch-from-upstream",
                "",
                False,
                "",
                git_mixins.branches.Upstream(
                    ".", "update-branch-from-upstream", "update-branch-from-upstream", ""
                )
            )
        ])


TMPDIR_PREFIX = "GitSavvy-end-to-end-test-"


class EndToEndTestCase(DeferrableTestCase):
    def setUp(self):
        s = sublime.load_settings("Preferences.sublime-settings")
        s.set("close_windows_when_empty", False)

        self.tmp_dir = tmp_dir = tempfile.mkdtemp(prefix=TMPDIR_PREFIX)
        self.addCleanup(lambda: rmdir(self.tmp_dir))
        self.window = window = self.new_window()

        project_data = dict(folders=[dict(follow_symlinks=True, path=tmp_dir)])
        window.set_project_data(project_data)
        yield lambda: any(d for d in window.folders() if d == tmp_dir)

    def init_repo(self) -> GitCommand:
        repo = GitCommand()
        repo.window = self.window  # type: ignore[attr-defined]
        repo.git("init", working_dir=self.tmp_dir)
        repo.git("commit", "-m", "Initial commit", "--allow-empty")
        return repo

    def new_window(self):
        sublime.run_command("new_window")
        window = sublime.active_window()
        self.addCleanup(lambda: window.run_command("close_window"))
        return window


def rmdir(path):
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        subprocess.Popen(
            "rmdir /s /q {}".format(path),
            shell=True,
            startupinfo=startupinfo)
    else:
        shutil.rmtree(path, ignore_errors=True)


class TestBranchParsing(EndToEndTestCase):
    def test_current_branch_is_master(self):
        repo = self.init_repo()
        branch = repo.get_current_branch_name()
        self.assertEqual(branch, "master")

    def test_active_local_branch(self):
        repo = self.init_repo()
        commit_hash = repo.get_commit_hash_for_head()
        actual = list(repo.get_branches())
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "master",
                None,
                "master",
                commit_hash,
                "Initial commit",
                "",
                "",
                True,
                "",
                None
            )
        ])

    def test_tracking_local_branch(self):
        repo = self.init_repo()
        commit_hash = repo.get_commit_hash_for_head()
        repo.git("checkout", "--track", "-b", "feature-branch")

        actual = list(b for b in repo.get_branches() if b.name != "master")
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "feature-branch",
                None,
                "feature-branch",
                commit_hash,
                "Initial commit",
                "./master",
                "",
                True,
                "",
                git_mixins.branches.Upstream(
                    ".", "master", "master", ""
                )
            )
        ])
