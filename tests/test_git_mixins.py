from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.parameterized import parameterized as p, param

from GitSavvy.core.git_command import GitCommand


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
