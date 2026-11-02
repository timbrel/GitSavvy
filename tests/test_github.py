from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core.interfaces import show_commit
from GitSavvy.github import github
from GitSavvy.github.commands import pull_request
from GitSavvy.github.commands.pull_request import gs_github_pull_request
from GitSavvy.tests.mockito import any_, mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


class TestCommitChecks(DeferrableTestCase):
    def tearDown(self):
        unstub()
        show_commit.url_cache.clear()

    @p.expand([
        ("github.com", "api.github.com", "", "token"),
        ("github.com", "api.github.com", "", None),
        ("github.example.com", "github.example.com", "/api/v3", "token"),
    ])
    def test_checks_commit_using_api_head(self, host, api_host, prefix, token):
        repository = github.GitHubRepo("", host, "owner", "repo", token)
        when(github.interwebs).request(
            "HEAD", api_host, 443, prefix + "/repos/owner/repo/commits/abc123?",
            auth=(token, "x-oauth-basic") if token else None,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        ).thenReturn(github.interwebs.Response(b"", {}, 200, True))

        self.assertTrue(github.commit_exists("abc123", repository))

    @p.expand([(404, False), (422, False), (200, True)])
    def test_commit_presence(self, status, expected):
        repository = github.GitHubRepo("", "github.com", "owner", "repo", "token")
        when(github.interwebs).request(...).thenReturn(github.interwebs.Response(b"", {}, status, True))

        self.assertEqual(expected, github.commit_exists("abc123", repository))

    @p.expand([(401,), (403,), (500,)])
    def test_errors_are_not_reported_as_missing_commits(self, status):
        repository = github.GitHubRepo("", "github.com", "owner", "repo", "token")
        when(github.interwebs).request(...).thenReturn(github.interwebs.Response(b"", {}, status, True))

        with self.assertRaises(github.FailedGithubRequest):
            github.commit_exists("abc123", repository)

    @p.expand([(True,), (False,)])
    def test_open_command_checks_api_but_opens_website(self, exists):
        url = "https://github.com/owner/repo"
        repository = github.GitHubRepo(url, "github.com", "owner", "repo", "token")
        view = mock()
        command = mock({"view": view})
        when(command).commits().thenReturn(iter(["abc123"]))
        when(command).get_integrated_remote_url().thenReturn(url)
        when(github).parse_remote(url).thenReturn(repository)
        when(github).commit_exists("abc123", repository).thenReturn(exists)
        when(show_commit).open_in_browser(url + "/commit/abc123").thenReturn(None)
        when(show_commit).flash(view, ...).thenReturn(None)

        show_commit.gs_show_commit_open_on_github.run(command, None)

        verify(show_commit, times=int(exists)).open_in_browser(url + "/commit/abc123")
        verify(show_commit, times=int(not exists)).flash(view, "commit abc123 not found on " + url)

    @p.expand([(True,), (False,)])
    def test_annotation_checks_api(self, exists):
        url = "https://github.com/owner/repo"
        repository = github.GitHubRepo(url, "github.com", "owner", "repo", "token")
        command = mock()
        when(command).get_integrated_remote_url().thenReturn(url)
        when(show_commit).cached_until_focus_switch(command.get_integrated_remote_url).thenReturn(url)
        when(github).parse_remote(url).thenReturn(repository)
        when(github).commit_exists("abc123", repository).thenReturn(exists)
        expected_url = url + "/commit/abc123" if exists else None
        when(show_commit).ensure_on_ui(...).thenReturn(None)

        show_commit.gs_show_commit_refresh.annotate_with_github_link.__wrapped__(command, "abc123")

        verify(show_commit).ensure_on_ui(any_(), expected_url)
        self.assertEqual(expected_url, show_commit.url_cache.get("abc123"))


class TestPullRequestDiff(DeferrableTestCase):
    def tearDown(self):
        unstub()

    @p.expand([
        ("github.com", "api.github.com", "", "token"),
        ("github.com", "api.github.com", "", None),
        ("github.example.com", "github.example.com", "/api/v3", "token"),
    ])
    def test_downloads_diff_from_api(self, host, api_host, prefix, token):
        repository = github.GitHubRepo("", host, "owner", "repo", token)
        response = github.interwebs.Response(b"diff --git a/file b/file\n", {}, 200, False)
        when(github.interwebs).get(
            api_host, 443, prefix + "/repos/owner/repo/pulls/42?",
            auth=(token, "x-oauth-basic") if token else None,
            headers={"Accept": "application/vnd.github.diff", "X-GitHub-Api-Version": "2022-11-28"}
        ).thenReturn(response)

        self.assertEqual("diff --git a/file b/file\n", github.get_pull_request_diff(42, repository))

    @p.expand([(401,), (403,), (404,), (500,)])
    def test_rejects_error_responses(self, status):
        repository = github.GitHubRepo("", "github.com", "owner", "repo", "token")
        when(github.interwebs).get(...).thenReturn(
            github.interwebs.Response(b"error page", {}, status, False)
        )

        with self.assertRaises(github.FailedGithubRequest):
            github.get_pull_request_diff(42, repository)

    def test_command_uses_authenticated_diff_helper(self):
        repository = github.GitHubRepo("", "github.com", "owner", "repo", "token")
        window, view = mock(), mock()
        command = mock({"window": window, "base_remote": repository, "pr": {"number": 42}})
        when(github).get_pull_request_diff(42, repository).thenReturn("diff content")
        when(pull_request).create_scratch_view(window, "pr_diff", ...).thenReturn(view)
        when(pull_request).replace_view_content(view, "diff content").thenReturn(None)

        gs_github_pull_request.view_diff_for_pr(command)

        verify(pull_request).replace_view_content(view, "diff content")


class TestMissingPullRequestHead(DeferrableTestCase):
    def tearDown(self):
        unstub()

    @p.expand([(0, "checkout_detached"), (3, "view_diff_for_pr"), (4, "open_pr_in_browser")])
    def test_non_checkout_actions_do_not_require_head_repo(self, index, action):
        future = mock()
        when(future).result(4.0).thenReturn({"number": 42, "head": {"repo": None}})
        command = mock({"pr_": future})
        getattr(when(command), action)().thenReturn(None)

        gs_github_pull_request.on_select_action(command, index)

        getattr(verify(command), action)()


    def test_detached_checkout_fetches_base_repo_pr_ref(self):
        window = mock()
        command = mock({
            "window": window,
            "base_remote_name": "upstream",
            "pr": {"number": 42, "head": {"repo": None}},
        })
        when(window).status_message(...).thenReturn(None)
        when(window).run_command("gs_checkout_branch", {"branch": "FETCH_HEAD"}).thenReturn(None)
        when(command).git("fetch", "upstream", "refs/pull/42/head").thenReturn("")

        gs_github_pull_request.checkout_detached(command)

        verify(command).git("fetch", "upstream", "refs/pull/42/head")
        verify(window).run_command("gs_checkout_branch", {"branch": "FETCH_HEAD"})


    @p.expand([(1, True), (2, False)])
    def test_branch_action_suggests_pr_number(self, index, checkout):
        future = mock()
        when(future).result(4.0).thenReturn({"number": 42, "head": {"repo": None}})
        command = mock({"pr_": future})
        callbacks = []
        when(pull_request).show_single_line_input_panel(
            "Enter branch name for PR 42:", "pr-42", ...
        ).thenAnswer(lambda caption, name, callback: callbacks.append(callback))
        when(command).create_branch_for_pr("chosen-name", checkout=checkout).thenReturn(None)

        gs_github_pull_request.on_select_action(command, index)
        callbacks[0]("chosen-name")

        verify(command).create_branch_for_pr("chosen-name", checkout=checkout)

    @p.expand([(False,), (True,)])
    def test_local_branch_has_no_upstream(self, checkout):
        window = mock()
        command = mock({
            "window": window,
            "base_remote_name": "upstream",
            "pr": {"number": 42, "head": {"repo": None}},
        })
        when(window).status_message(...).thenReturn(None)
        when(command).git("fetch", "upstream", "refs/pull/42/head").thenReturn("")
        when(command).git("branch", "--no-track", "pr-42", "FETCH_HEAD").thenReturn("")
        when(window).run_command("gs_checkout_branch", {"branch": "pr-42"}).thenReturn(None)
        when(pull_request.util.view).refresh_gitsavvy_interfaces(window).thenReturn(None)
        when(pull_request.sublime).yes_no_cancel_dialog(...).thenRaise(AssertionError("Unexpected prompt"))

        gs_github_pull_request.create_branch_for_pr(command, "pr-42", checkout=checkout)

        verify(command).git("fetch", "upstream", "refs/pull/42/head")
        verify(command).git("branch", "--no-track", "pr-42", "FETCH_HEAD")
        verify(window, times=int(checkout)).run_command("gs_checkout_branch", {"branch": "pr-42"})
        verify(pull_request.util.view, times=int(not checkout)).refresh_gitsavvy_interfaces(window)


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
