import os
import shutil
import subprocess
import sys
import tempfile
import time

import sublime

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.parameterized import parameterized as p, param

from GitSavvy.core.git_command import GitCommand
from GitSavvy.core.exceptions import GitSavvyError
from GitSavvy.core import git_mixins
from GitSavvy.core.git_mixins.worktrees import Worktree, WorktreesMixin
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


class TestAheadBehindBackgroundProbes(TestGitMixinsUsage):
    def setUp(self):
        self.scheduled_tasks = []
        self.scheduled_delays = []
        when(git_mixins.branches).run_on_new_thread(...).thenAnswer(
            lambda fn, *args, **kwargs: fn(*args, **kwargs)
        )
        when(git_mixins.branches).run_when_worker_is_idle(...).thenAnswer(
            self.schedule_task
        )
        when(git_mixins.branches).run_when_worker_is_idle_after(...).thenAnswer(
            self.schedule_delayed_task
        )

    def test_missing_head_retries_without_ahead_behind(self):
        repo = SlowBranchesRepo(head_missing=True)

        repo.get_branches()

        self.assertEqual(repo.ahead_behind_queries, 1)
        self.assertEqual(repo.plain_queries, 1)
        self.assertEqual(self.scheduled_tasks, [])

    def test_a_hiccup_is_confirmed_in_the_background(self):
        repo = SlowBranchesRepo()

        repo.get_branches()

        self.assertEqual(repo.ahead_behind_queries, 1)
        self.assertEqual(repo.plain_queries, 1)
        self.assertEqual(repo.commit_graph_writes, 0)
        self.assertEqual(repo.state["ahead_behind_consecutive_failures"], 1)

        self.run_all_tasks()

        self.assertEqual(repo.ahead_behind_queries, 2)
        self.assertEqual(repo.commit_graph_writes, 1)
        self.assertEqual(repo.state["ahead_behind_consecutive_failures"], 0)
        self.assertEqual(repo.state["ahead_behind_retry_at"], 0)

    def test_successful_probe_replaces_the_fallback_cache(self):
        repo = SlowBranchesRepo(branch_output=True)

        repo.get_branches()
        self.assertIsNone(repo.state["branches"][0].distance_to_head)

        self.run_all_tasks()

        self.assertEqual(
            repo.state["branches"][0].distance_to_head,
            git_mixins.branches.AheadBehind(2, 4)
        )

    def test_a_slow_repo_is_confirmed_after_five_background_probes(self):
        now = time.time()
        repo = SlowBranchesRepo(timeouts=6)

        repo.get_branches()
        self.run_all_tasks()

        self.assertEqual(repo.ahead_behind_queries, 6)
        self.assertEqual(self.scheduled_delays, [1000, 10000, 60000, 600000])
        self.assertEqual(repo.plain_queries, 1)
        self.assertEqual(repo.commit_graph_writes, 1)
        self.assertEqual(repo.state["ahead_behind_consecutive_failures"], 2)
        self.assertGreaterEqual(
            repo.state["ahead_behind_retry_at"],
            now + git_mixins.branches.AHEAD_BEHIND_SLOW_RETRY_INTERVAL
        )

    def test_commit_graph_write_is_rate_limited(self):
        repo = SlowBranchesRepo({"last_commit_graph_write": time.time()})

        repo.get_branches()
        self.run_all_tasks()

        self.assertEqual(repo.commit_graph_writes, 0)
        self.assertEqual(repo.ahead_behind_queries, 2)
        self.assertEqual(repo.state["ahead_behind_consecutive_failures"], 0)

    def test_known_slow_repo_uses_the_fast_query_before_retry_time(self):
        repo = SlowBranchesRepo({
            "ahead_behind_consecutive_failures": 2,
            "ahead_behind_retry_at": time.time() + 60
        })

        repo.get_branches()

        self.assertEqual(repo.ahead_behind_queries, 0)
        self.assertEqual(repo.plain_queries, 1)
        self.assertEqual(self.scheduled_tasks, [])

    def test_due_probe_does_not_interrupt_a_known_slow_repo(self):
        repo = SlowBranchesRepo({
            "ahead_behind_consecutive_failures": 2,
            "ahead_behind_retry_at": time.time() - 1,
            "last_commit_graph_write": time.time()
        }, timeouts=0)

        repo.get_branches()

        self.assertEqual(repo.ahead_behind_queries, 0)
        self.assertEqual(repo.plain_queries, 1)
        self.assertEqual(len(self.scheduled_tasks), 1)

        self.run_all_tasks()

        self.assertEqual(repo.ahead_behind_queries, 1)
        self.assertEqual(repo.state["ahead_behind_consecutive_failures"], 0)

    def test_duplicate_schedules_only_start_one_probe(self):
        state = {
            "ahead_behind_consecutive_failures": 2,
            "ahead_behind_retry_at": time.time() - 1,
            "last_commit_graph_write": time.time()
        }
        repo = SlowBranchesRepo(state, timeouts=0)

        repo.get_branches()
        repo.get_branches()
        self.run_all_tasks()

        self.assertEqual(repo.ahead_behind_queries, 1)

    def test_known_slow_repo_only_gets_three_probes(self):
        now = time.time()
        repo = SlowBranchesRepo({
            "ahead_behind_consecutive_failures": 2,
            "ahead_behind_retry_at": now,
            "last_commit_graph_write": now
        }, timeouts=5)

        repo.get_branches()
        self.run_all_tasks()

        self.assertEqual(repo.ahead_behind_queries, 3)
        self.assertEqual(self.scheduled_delays, [60000, 600000])
        self.assertEqual(repo.state["ahead_behind_consecutive_failures"], 3)
        self.assertGreaterEqual(
            repo.state["ahead_behind_retry_at"],
            now + git_mixins.branches.AHEAD_BEHIND_SLOW_RETRY_INTERVAL
        )

    def schedule_task(self, fn, *args, **kwargs):
        kwargs.pop("after", None)
        self.scheduled_tasks.append((fn, args, kwargs))

    def schedule_delayed_task(self, after, fn, *args, **kwargs):
        self.scheduled_delays.append(after)
        self.schedule_task(fn, *args, **kwargs)

    def run_next_task(self):
        fn, args, kwargs = self.scheduled_tasks.pop(0)
        fn(*args, **kwargs)

    def run_all_tasks(self):
        while self.scheduled_tasks:
            self.run_next_task()


class SlowBranchesRepo(git_mixins.branches.BranchesMixin):
    def __init__(
        self,
        state=None,
        *,
        timeouts=1,
        branch_output=False,
        head_missing=False
    ):
        self.state = state or {}
        self.timeouts = timeouts
        self.branch_output = branch_output
        self.head_missing = head_missing
        self.ahead_behind_queries = 0
        self.plain_queries = 0
        self.commit_graph_writes = 0

    @property
    def git_version(self):
        return (2, 41, 0)

    def current_state(self):
        return self.state

    def update_store(self, partial_state):
        self.state.update(partial_state)

    def git_throwing_silently(self, command, *args, **kwargs):
        if command == "commit-graph":
            self.commit_graph_writes += 1
            return ""

        if any("%(ahead-behind:HEAD)" in str(arg) for arg in args):
            self.ahead_behind_queries += 1
            if self.head_missing:
                raise GitSavvyError(
                    "", stderr="fatal: failed to find 'HEAD'", show_panel=False
                )
            if self.ahead_behind_queries <= self.timeouts:
                raise GitSavvyError(
                    "", stderr="timed out after 0.2 seconds", show_panel=False
                )
            ahead_behind = "2 4"
        else:
            self.plain_queries += 1
            ahead_behind = ""

        if self.branch_output:
            return join0(
                [" ", "refs/heads/master", "refs/remotes/origin/master", "origin", ""]
                + date_sha_and_subject
                + [ahead_behind, ""]
            )
        return ""


class TestGetWorktreesParsing(TestGitMixinsUsage):
    def test_old_git_uses_non_z_parser(self):
        repo = WorktreesTestRepo(
            (2, 34, 1),
            "\n".join((
                "worktree C:/Users/c-flo/AppData/Roaming/Sublime Text/Packages/GitSavvy",
                "HEAD 0da1384c809f837b835c335ffdc513337308f1d8",
                "detached",
                "",
                "worktree C:/Users/c-flo/Dev/GitSavvy-enhance-merge-branch-list",
                "HEAD 72021faf22b1377d3c4b72b350a95ea71f585efb",
                "branch refs/heads/enhance-merge-branch-list",
                ""
            )),
            "C:/Users/c-flo/AppData/Roaming/Sublime Text/Packages/GitSavvy"
        )

        actual = repo.get_worktrees()

        self.assertEqual(repo.git_args, ("worktree", "list", "--porcelain"))
        self.assertEqual(actual, [
            Worktree(
                "C:/Users/c-flo/AppData/Roaming/Sublime Text/Packages/GitSavvy",
                "0da1384c809f837b835c335ffdc513337308f1d8",
                None,
                True,
                False,
                False
            ),
            Worktree(
                "C:/Users/c-flo/Dev/GitSavvy-enhance-merge-branch-list",
                "72021faf22b1377d3c4b72b350a95ea71f585efb",
                "enhance-merge-branch-list",
                False,
                False,
                False
            )
        ])

    def test_old_git_drops_malformed_non_z_output(self):
        repo = WorktreesTestRepo(
            (2, 34, 1),
            "\n".join((
                "worktree /tmp/work",
                "tree",
                "HEAD 0da1384c809f837b835c335ffdc513337308f1d8",
                "branch refs/heads/master",
                ""
            )),
            "/tmp/work\ntree"
        )

        actual = repo.get_worktrees()

        self.assertEqual(actual, [])

    def test_old_git_drops_quoted_non_z_paths(self):
        repo = WorktreesTestRepo(
            (2, 34, 1),
            "\n".join((
                'worktree "/tmp/work\\ntree"',
                "HEAD 0da1384c809f837b835c335ffdc513337308f1d8",
                "branch refs/heads/master",
                ""
            )),
            "/tmp/work\ntree"
        )

        actual = repo.get_worktrees()

        self.assertEqual(actual, [])


class TestCreateNewWorktreeProject(TestGitMixinsUsage):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix=TMPDIR_PREFIX)
        self.addCleanup(lambda: rmdir(self.tmp_dir))
        self.repo_path = os.path.join(self.tmp_dir, "repo")
        os.makedirs(self.repo_path)
        self.project_file = os.path.join(self.repo_path, "repo.sublime-project")
        self.project_data = {
            "folders": [
                {"path": self.repo_path},
                {"path": ".", "folder_exclude_patterns": [".git"]}
            ]
        }

    def test_copies_an_untracked_local_project(self):
        repo = WorktreeCreationTestRepo(
            self.repo_path, self.project_file, self.project_data
        )
        worktree_path = os.path.join(self.tmp_dir, "worktree")

        repo.create_new_worktree("abc123", worktree_path)

        with open(os.path.join(worktree_path, "repo.sublime-project"), encoding="utf-8") as file:
            copied_data = sublime.decode_value(file.read())
        self.assertEqual(copied_data, {
            "folders": [
                {"path": "."},
                {"path": ".", "folder_exclude_patterns": [".git"]}
            ]
        })
        self.assertEqual(self.project_data["folders"][0]["path"], self.repo_path)

    def test_respects_the_copy_project_setting(self):
        repo = WorktreeCreationTestRepo(
            self.repo_path, self.project_file, self.project_data,
            copy_project=False
        )
        worktree_path = os.path.join(self.tmp_dir, "worktree")

        repo.create_new_worktree("abc123", worktree_path)

        self.assertFalse(os.path.exists(os.path.join(worktree_path, "repo.sublime-project")))

    def test_does_not_copy_a_non_local_project(self):
        repo = WorktreeCreationTestRepo(
            self.repo_path,
            os.path.join(self.tmp_dir, "another-repo", "repo.sublime-project"),
            self.project_data
        )
        worktree_path = os.path.join(self.tmp_dir, "worktree")

        repo.create_new_worktree("abc123", worktree_path)

        self.assertFalse(os.path.exists(os.path.join(worktree_path, "repo.sublime-project")))

    def test_does_not_copy_a_tracked_project(self):
        repo = WorktreeCreationTestRepo(
            self.repo_path, self.project_file, self.project_data,
            tracked=True
        )
        worktree_path = os.path.join(self.tmp_dir, "worktree")

        repo.create_new_worktree("abc123", worktree_path)

        self.assertFalse(os.path.exists(os.path.join(worktree_path, "repo.sublime-project")))

    def test_does_not_overwrite_a_project_from_the_start_point(self):
        repo = WorktreeCreationTestRepo(
            self.repo_path, self.project_file, self.project_data,
            checked_out_project="from the start point"
        )
        worktree_path = os.path.join(self.tmp_dir, "worktree")

        repo.create_new_worktree("abc123", worktree_path)

        with open(os.path.join(worktree_path, "repo.sublime-project"), encoding="utf-8") as file:
            self.assertEqual(file.read(), "from the start point")


class WorktreeCreationTestRepo(WorktreesMixin):
    def __init__(
        self,
        repo_path,
        project_file,
        project_data,
        *,
        tracked=False,
        checked_out_project=None,
        copy_project=True
    ):
        self._repo_path = repo_path
        self._app_settings = {
            "copy_active_project_file_to_new_worktrees": copy_project
        }
        self.window = WorktreeCreationTestWindow(project_file, project_data)
        self.tracked = tracked
        self.checked_out_project = checked_out_project

    @property
    def app_settings(self):
        return self._app_settings

    @property
    def repo_path(self):
        return self._repo_path

    def git(self, command, *args, **kwargs):
        if command == "worktree":
            worktree_path = args[1]
            os.makedirs(worktree_path)
            if self.checked_out_project is not None:
                filename = os.path.basename(self.window.project_file_name())
                with open(os.path.join(worktree_path, filename), "w", encoding="utf-8") as file:
                    file.write(self.checked_out_project)
            return ""
        if command == "ls-files":
            return "tracked\0" if self.tracked else ""
        raise AssertionError("Unexpected git command: {} {}".format(command, args))


class WorktreeCreationTestWindow:
    def __init__(self, project_file, project_data):
        self.project_file = project_file
        self.data = project_data

    def project_file_name(self):
        return self.project_file

    def project_data(self):
        return self.data


class WorktreesTestRepo(WorktreesMixin):
    def __init__(self, git_version, stdout, repo_path):
        self._git_version = git_version
        self.stdout = stdout
        self._repo_path = repo_path
        self.git_args = None
        self.store = None

    @property
    def git_version(self):
        return self._git_version

    @property
    def repo_path(self):
        return self._repo_path

    def git(self, *args, **kwargs):
        self.git_args = args
        return self.stdout

    def update_store(self, partial_state):
        self.store = partial_state


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
