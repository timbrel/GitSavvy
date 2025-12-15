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
from GitSavvy.core.utils import resolve_path


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


date_sha_and_subject = ["0", "now", "now", "89b79cd737465ed308ecc00289d00a6f923f2da5", "The Subject"]
join0 = lambda x: "\x00".join(x)


class TestGetBranchesParsing(TestGitMixinsUsage):
    def test_local_branch(self):
        repo = GitCommand()
        git_output = join0(
            [" ", "refs/heads/master", "refs/remotes/origin/master", "origin", ""]
            + date_sha_and_subject
            + ["0 0", "local_worktree_path"])
        when(repo).git("for-each-ref", ...).thenReturn(git_output)
        when(repo).get_repo_path().thenReturn("yeah/sure")
        actual = repo.get_branches()
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "master",
                None,
                "master",
                "89b79cd737465ed308ecc00289d00a6f923f2da5",
                "The Subject",
                False,
                False,
                0,
                "now",
                "now",
                git_mixins.branches.Upstream(
                    "origin", "master", "origin/master", ""
                ),
                git_mixins.branches.AheadBehind(ahead=0, behind=0),
                "local_worktree_path"
            )
        ])

    def test_active_local_branch(self):
        repo = GitCommand()
        git_output = join0(
            ["*", "refs/heads/master", "refs/remotes/origin/master", "origin", ""]
            + date_sha_and_subject
            + ["2 4", ""])
        when(repo).git("for-each-ref", ...).thenReturn(git_output)
        when(repo).get_repo_path().thenReturn("yeah/sure")
        actual = repo.get_branches()
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "master",
                None,
                "master",
                "89b79cd737465ed308ecc00289d00a6f923f2da5",
                "The Subject",
                True,
                False,
                0,
                "now",
                "now",
                git_mixins.branches.Upstream(
                    "origin", "master", "origin/master", ""
                ),
                git_mixins.branches.AheadBehind(ahead=2, behind=4),
                None
            )
        ])

    def test_remote_branch(self):
        repo = GitCommand()
        git_output = join0(
            [" ", "refs/remotes/origin/dev", "", "", ""]
            + date_sha_and_subject
            + ["0 0", ""])
        when(repo).git("for-each-ref", ...).thenReturn(git_output)
        when(repo).get_repo_path().thenReturn("yeah/sure")
        actual = repo.get_branches()
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "dev",
                "origin",
                "origin/dev",
                "89b79cd737465ed308ecc00289d00a6f923f2da5",
                "The Subject",
                False,
                True,
                0,
                "now",
                "now",
                None,
                git_mixins.branches.AheadBehind(ahead=0, behind=0),
                None
            )
        ])

    def test_upstream_with_dashes_in_name(self):
        repo = GitCommand()
        git_output = join0(
            [" ", "refs/heads/master", "refs/remotes/orig/in/master", "orig/in", ""]
            + date_sha_and_subject
            + ["0 0", ""])
        when(repo).git("for-each-ref", ...).thenReturn(git_output)
        when(repo).get_repo_path().thenReturn("yeah/sure")
        actual = repo.get_branches()
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "master",
                None,
                "master",
                "89b79cd737465ed308ecc00289d00a6f923f2da5",
                "The Subject",
                False,
                False,
                0,
                "now",
                "now",
                git_mixins.branches.Upstream(
                    "orig/in", "master", "orig/in/master", ""
                ),
                git_mixins.branches.AheadBehind(ahead=0, behind=0),
                None
            )
        ])

    def test_tracking_status(self):
        repo = GitCommand()
        git_output = join0(
            [" ", "refs/heads/master", "refs/remotes/origin/master", "origin", "gone"]
            + date_sha_and_subject
            + ["0 0", ""])
        when(repo).git("for-each-ref", ...).thenReturn(git_output)
        when(repo).get_repo_path().thenReturn("yeah/sure")
        actual = repo.get_branches()
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "master",
                None,
                "master",
                "89b79cd737465ed308ecc00289d00a6f923f2da5",
                "The Subject",
                False,
                False,
                0,
                "now",
                "now",
                git_mixins.branches.Upstream(
                    "origin", "master", "origin/master", "gone"
                ),
                git_mixins.branches.AheadBehind(ahead=0, behind=0),
                None
            )
        ])

    def test_tracking_local_branch(self):
        repo = GitCommand()
        git_output = join0(
            [" ", "refs/heads/test", "refs/heads/update-branch-from-upstream", ".", ""]
            + date_sha_and_subject
            + ["0 0", ""])
        when(repo).git("for-each-ref", ...).thenReturn(git_output)
        when(repo).get_repo_path().thenReturn("yeah/sure")
        actual = repo.get_branches()
        self.assertEqual(actual, [
            git_mixins.branches.Branch(
                "test",
                None,
                "test",
                "89b79cd737465ed308ecc00289d00a6f923f2da5",
                "The Subject",
                False,
                False,
                0,
                "now",
                "now",
                git_mixins.branches.Upstream(
                    ".", "update-branch-from-upstream", "update-branch-from-upstream", ""
                ),
                git_mixins.branches.AheadBehind(ahead=0, behind=0),
                None
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

    def init_repo(self, comitterdate=None) -> GitCommand:
        repo = GitCommand()
        repo.window = self.window  # type: ignore[attr-defined]
        repo.git("init", working_dir=self.tmp_dir)
        repo.git(
            "commit",
            "-m", "Initial commit",
            "--allow-empty",
            custom_environ={"GIT_COMMITTER_DATE": comitterdate} if comitterdate else {}
        )
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
        repo = self.init_repo(comitterdate="1671490333 +0000")
        commit_hash = repo.get_commit_hash_for_head()
        actual = repo.get_branches()
        self.assertEqual(
            list(map(lambda b: b._replace(relative_committerdate="long ago"), actual)),
            [
                git_mixins.branches.Branch(
                    "master",
                    None,
                    "master",
                    commit_hash,
                    "Initial commit",
                    True,
                    False,
                    1671490333,
                    "Dec 19 2022",
                    "long ago",
                    None,
                    git_mixins.branches.AheadBehind(ahead=0, behind=0),
                    # Normalize to the canonical path so 8.3 short names
                    # (e.g. RUNNER~1 on Windows) don't break the assertion.
                    resolve_path(repo.repo_path).replace("\\", "/")
                )
            ]
        )

    def test_tracking_local_branch(self):
        repo = self.init_repo(comitterdate="1671490333 +0000")
        commit_hash = repo.get_commit_hash_for_head()
        repo.git("checkout", "--track", "-b", "feature-branch")

        actual = list(b for b in repo.get_branches() if b.name != "master")
        self.assertEqual(
            list(map(lambda b: b._replace(relative_committerdate="long ago"), actual)),
            [
                git_mixins.branches.Branch(
                    "feature-branch",
                    None,
                    "feature-branch",
                    commit_hash,
                    "Initial commit",
                    True,
                    False,
                    1671490333,
                    "Dec 19 2022",
                    "long ago",
                    git_mixins.branches.Upstream(
                        ".", "master", "master", ""
                    ),
                    git_mixins.branches.AheadBehind(ahead=0, behind=0),
                    # Normalize to the canonical path so 8.3 short names
                    # (e.g. RUNNER~1 on Windows) don't break the assertion.
                    resolve_path(repo.repo_path).replace("\\", "/")
                )
            ]
        )
