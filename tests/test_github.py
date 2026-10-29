from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.github import github
from GitSavvy.github.commands.pull_request import gs_github_pull_request
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


class TestPullRequestTransport(DeferrableTestCase):
    @p.expand([
        ("git@github.com:owner/repo.git", "ssh_url"),
        ("ssh://git@github.com/owner/repo.git", "ssh_url"),
        ("ssh://git@github.example.com:2222/owner/repo.git", "ssh_url"),
        ("https://github.com/owner/repo.git", "clone_url"),
    ])
    def test_preserves_transport(self, remote_url: str, expected_key: str) -> None:
        repo = {
            "ssh_url": "git@github.com:contributor/repo.git",
            "clone_url": "https://github.com/contributor/repo.git",
        }
        command = mock({"base_remote_url": remote_url, "pr": {"head": {"repo": repo}}})

        actual = gs_github_pull_request.best_remote_url_for_pr(command)

        self.assertEqual(repo[expected_key], actual)


class TestGitHubAuthentication(DeferrableTestCase):
    def tearDown(self):
        unstub()

    @p.expand([
        ("github.com", {}, "env-token", "env-token"),
        ("GITHUB.COM", {}, "env-token", "env-token"),
        ("github.com", {"github.com": "host-token"}, "env-token", "host-token"),
        ("github.com", {"github.com": ""}, "env-token", "env-token"),
        ("github.com", {}, None, None),
        ("github.example.com", {}, "env-token", None),
        ("github.example.com", {"github.com": "public-token"}, "env-token", None),
        ("github.example.com", {"github.example.com": "host-token"}, "env-token", "host-token"),
        ("github.example.com", {"github.example.com": ""}, "env-token", None),
        ("evilgithub.com", {}, "env-token", None),
        ("sub.github.com", {}, "env-token", None),
        ("github.com.example.com", {}, "env-token", None),
    ])
    def test_token_selection(
        self, host: str, tokens: dict[str, str], env_token: str | None, expected_token: str | None
    ) -> None:
        when(github.app_settings).get("api_tokens", {}).thenReturn(tokens)
        when(github.os.environ).get("GITHUB_TOKEN").thenReturn(env_token)

        repository = github.parse_remote("https://{}/owner/repo".format(host))

        self.assertEqual(expected_token, repository.token)

    @p.expand([
        ("github.example.com",),
        ("evilgithub.com",),
        ("sub.github.com",),
        ("github.com.example.com",),
    ])
    def test_query_does_not_send_env_token_to_other_hosts(self, host: str) -> None:
        when(github.app_settings).get("api_tokens", {}).thenReturn({})
        when(github.os.environ).get("GITHUB_TOKEN").thenReturn("env-token")
        response = mock({"status": 200, "is_json": True, "payload": {}})
        when(github.interwebs).get(
            host, 443, "/api/v3/repos/owner/repo?", auth=None
        ).thenReturn(response)
        repository = github.parse_remote("https://{}/owner/repo".format(host))

        github.get_repo_data(repository)

        verify(github.interwebs).get(
            host, 443, "/api/v3/repos/owner/repo?", auth=None
        )

    @p.expand([
        ("github.com", (False, "api.github.com")),
        ("GITHUB.COM", (False, "api.github.com")),
        ("github.example.com", (True, "github.example.com")),
        ("evilgithub.com", (True, "evilgithub.com")),
        ("sub.github.com", (True, "sub.github.com")),
    ])
    def test_api_host_selection(self, host: str, expected: tuple) -> None:
        repository = github.GitHubRepo("", host, "owner", "repo", None)

        self.assertEqual(expected, github.get_api_fqdn(repository))


class TestGitHubRepoCreation(DeferrableTestCase):
    def tearDown(self):
        unstub()

    @p.expand([(False,), (True,)])
    def test_create_user_repo(self, private: bool) -> None:
        payload = {"name": "new-repo", "private": private}
        response = mock({"payload": payload})
        when(github.interwebs).post(
            "api.github.com", 443, "/user/repos",
            auth=("token", "x-oauth-basic"), payload=payload
        ).thenReturn(response)
        when(github).validate_response(response, method="POST").thenReturn(None)

        result = github.create_user_repo("token", "new-repo", private=private)

        self.assertEqual(payload, result)
        verify(github).validate_response(response, method="POST")

    def test_create_user_repo_is_public_by_default(self) -> None:
        payload = {"name": "new-repo", "private": False}
        when(github).create_repo(
            "token", None, "new-repo", private=False
        ).thenReturn(payload)

        self.assertEqual(payload, github.create_user_repo("token", "new-repo"))


class TestGitHubRemoteParsing(DeferrableTestCase):
    def tearDown(self):
        unstub()
        github.remote_to_url.cache_clear()
        github._resolve_ssh_hostname.cache_clear()

    @p.expand([
        ("git://github.com/timbrel/GitSavvy.git", "https://github.com/timbrel/GitSavvy"),
        ("https://github.com/timbrel/GitSavvy.git", "https://github.com/timbrel/GitSavvy"),
        ("https://github.com/timbrel/GitSavvy", "https://github.com/timbrel/GitSavvy"),
    ])
    def test_remote_to_url_non_ssh_remotes(self, remote_url, expected):
        self.assertEqual(expected, github.remote_to_url(remote_url))

    def test_remote_to_url_resolves_host_alias_for_scp_like_ssh_remote(self):
        when(github)._resolve_ssh_hostname("my-github").thenReturn("github.com")

        actual = github.remote_to_url("git@my-github:me/my-project.git")

        self.assertEqual("https://github.com/me/my-project", actual)

    def test_remote_to_url_resolves_host_alias_for_ssh_scheme_remote(self):
        when(github)._resolve_ssh_hostname("my-github").thenReturn("github.com")

        actual = github.remote_to_url("ssh://git@my-github/me/my-project.git")

        self.assertEqual("https://github.com/me/my-project", actual)

    def test_read_ssh_config_hostname(self):
        when(github.subprocess).check_output(
            ["ssh", "-G", "my-github"],
            stderr=github.subprocess.DEVNULL,
            text=True,
            timeout=1.0,
            startupinfo=github.STARTUPINFO,
        ).thenReturn("host my-github\nhostname github.com\n")

        actual = github._read_ssh_config_hostname("my-github")

        self.assertEqual("github.com", actual)

    def test_resolve_ssh_hostname_falls_back_to_input_value(self):
        when(github)._read_ssh_config_hostname("my-github").thenReturn(None)

        actual = github._resolve_ssh_hostname("my-github")

        self.assertEqual("my-github", actual)
