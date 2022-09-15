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
            )
        ])

