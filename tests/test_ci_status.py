from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core import store
from GitSavvy.core.git_mixins.remotes import (
    ConfigEntry,
    PushDestination,
    PushedRef,
    RemoteInfo,
    RemoteInfoBlob,
    parse_push_porcelain
)
from GitSavvy.github import ci_status, github
from GitSavvy.github.github import CiObservation, GitHubRepo, PushTarget
from GitSavvy.tests.mockito import any as ANY, mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


REPO = "/fake/ci-repo"
OID = "a" * 40
OTHER_OID = "b" * 40
KEY = ("feature", OID)
UPSTREAM_URL = "git@github.com:upstream/project.git"
UPSTREAM = GitHubRepo("https://github.com/upstream/project", "github.com", "upstream", "project", "secret")
TARGET = PushTarget("me", "project", "refs/heads/feature")


class CiWatcherTestCase(DeferrableTestCase):
    def setUp(self):
        self.clock = 1000.0
        self.requests: list[tuple[ci_status.Run, PushTarget | None]] = []
        when(ci_status).now().thenAnswer(lambda: self.clock)
        when(ci_status).request_observation(...).thenAnswer(
            lambda run: self.requests.append((run, run.push_target))
        )
        when(ci_status).ensure_ticking().thenReturn(None)
        when(ci_status).has_associated_window(...).thenReturn(True)
        when(ci_status).is_enabled().thenReturn(True)
        store.state.pop(REPO, None)

    def tearDown(self):
        unstub()
        ci_status.runs.clear()
        ci_status.known_sources.clear()
        store.state.pop(REPO, None)

    def start(self, key=KEY, repository=UPSTREAM, url=UPSTREAM_URL):
        ci_status.start_run(REPO, key, repository, TARGET, url)

    def at(self, seconds: float) -> None:
        self.clock = 1000.0 + seconds
        ci_status.advance_all()

    def respond(self, state, **kwargs) -> None:
        run, _ = self.requests.pop(0)
        ci_status.on_observation(run, CiObservation(state, **kwargs))

    def respond_at(self, seconds: float, state, **kwargs) -> None:
        self.clock = 1000.0 + seconds
        self.respond(state, **kwargs)

    def text(self, key=KEY):
        return store.current_state(REPO)["github_ci_status"].get(key)


class TestPendingPresentation(CiWatcherTestCase):
    @p.expand([
        (0.0, "[CI: ◇]"),
        (0.199, "[CI: ◇]"),
        (0.2, "[CI: ◈]"),
        (0.4, "[CI: ◆]"),
        (0.6, "[CI: ◇]"),
        (2.999, "[CI: ◆]"),
        (3.0, "[◇]"),
        (3.2, "[◈]"),
        (3.4, "[◆]"),
    ])
    def test_frames_and_label_transition(self, elapsed, expected):
        self.assertEqual(expected, ci_status.pending_text(elapsed))

    def test_publishes_busy_text_immediately_and_animates(self):
        self.start()
        self.assertEqual("[CI: ◇]", self.text())

        self.at(0.2)
        self.assertEqual("[CI: ◈]", self.text())

        self.at(3.0)
        self.assertEqual("[◇]", self.text())

    def test_cache_updates_notify_subscribers(self):
        notifications = []
        unsubscribe = store.subscribe(REPO, {"github_ci_status"}, lambda *args: notifications.append(args))
        try:
            self.start()
        finally:
            unsubscribe()

        self.assertEqual(1, len(notifications))


class TestRequests(CiWatcherTestCase):
    def test_first_request_is_immediate_and_verifies_the_push_target(self):
        self.start()

        self.assertEqual(1, len(self.requests))
        self.assertEqual(TARGET, self.requests[0][1])

    def test_requests_never_overlap_and_follow_completion(self):
        self.start()
        self.at(30.0)
        self.assertEqual(1, len(self.requests))

        self.respond_at(31.0, "pending")
        self.at(40.9)
        self.assertEqual(0, len(self.requests))

        self.at(41.0)
        self.assertEqual(1, len(self.requests))
        self.assertIsNone(self.requests[0][1], "later requests omit the push verification")

    def test_unverified_target_is_retried_with_verification(self):
        self.start()
        self.respond_at(1.0, "unknown", transient=True)

        self.at(11.0)
        self.assertEqual(TARGET, self.requests[0][1])

    def test_no_request_without_associated_window(self):
        when(ci_status).has_associated_window(REPO).thenReturn(False)

        self.start()

        self.assertEqual([], self.requests)
        self.assertEqual("", self.text())
        self.assertEqual({}, ci_status.runs)


class TestSettling(CiWatcherTestCase):
    def test_success_needs_two_observations_ten_seconds_apart(self):
        self.start()
        self.respond_at(0.5, "success")
        self.assertTrue(self.text().startswith("[CI: "))
        self.assertNotEqual("[CI: ok]", self.text())

        self.at(10.5)
        self.respond_at(11.0, "success")
        self.assertEqual("[CI: ok]", self.text())

        self.at(15.999)
        self.assertEqual("[CI: ok]", self.text())

        self.at(16.0)
        self.assertEqual("", self.text())
        self.assertEqual({}, ci_status.runs)

        self.at(100.0)
        self.assertEqual([], self.requests)

    @p.expand([("pending",), ("no_checks",), ("unknown",)])
    def test_other_observations_reset_green_confirmation(self, interrupting_state):
        self.start()
        self.respond_at(0.5, "success")
        self.at(10.5)
        self.respond_at(11.0, interrupting_state, transient=True)
        self.at(21.0)
        self.respond_at(21.5, "success")
        self.assertNotEqual("[CI: ok]", self.text())

        self.at(31.5)
        self.respond_at(32.0, "success")
        self.assertEqual("[CI: ok]", self.text())

    def test_failure_settles_immediately_and_stays_red(self):
        self.start()
        self.respond_at(0.5, "pending")
        self.at(10.5)
        self.respond_at(11.0, "failure")
        self.assertEqual("[CI: failed]", self.text())

        self.at(15.999)
        self.assertEqual("[CI: failed]", self.text())

        self.at(16.0)
        self.assertEqual("[red]", self.text())
        self.assertEqual({}, ci_status.runs)

        self.at(100.0)
        self.assertEqual([], self.requests)

    def test_timed_transitions_happen_without_any_view(self):
        self.start()
        self.respond_at(0.5, "failure")

        self.at(6.0)

        self.assertEqual("[red]", self.text())


class TestTermination(CiWatcherTestCase):
    def test_no_checks_keep_busy_text_during_discovery_then_clear(self):
        self.start()
        t = 0.0
        while t < 50.0:
            self.respond_at(t + 0.5, "no_checks")
            self.assertTrue(self.text().startswith("["))
            self.assertNotIn(self.text(), ("", "[CI: ok]"))
            t += 10.5
            self.at(t)

        self.respond_at(60.0, "no_checks")

        self.assertEqual("", self.text())
        self.assertEqual({}, ci_status.runs)

    def test_checks_seen_disable_the_discovery_grace(self):
        self.start()
        self.respond_at(0.5, "pending")
        self.at(10.5)
        self.respond_at(61.0, "no_checks")

        self.assertIn(KEY, {key for _, key in ci_status.runs})

    def test_transient_failures_retry_after_10_20_40_seconds(self):
        self.start()
        self.respond_at(1.0, "unknown", transient=True)
        self.assertNotEqual("", self.text())
        for delay, failed_at in ((10.0, 1.0), (20.0, 11.0), (40.0, 31.0)):
            self.at(failed_at + delay - 0.001)
            self.assertEqual([], self.requests)
            self.at(failed_at + delay)
            self.assertEqual(1, len(self.requests))
            self.respond_at(failed_at + delay, "unknown", transient=True)

        self.assertEqual("", self.text())
        self.assertEqual({}, ci_status.runs)

    def test_valid_observation_resets_the_retry_count(self):
        self.start()
        self.respond_at(1.0, "unknown", transient=True)
        self.at(11.0)
        self.respond_at(11.0, "unknown", transient=True)
        self.at(31.0)
        self.respond_at(31.0, "pending")
        self.at(41.0)
        self.respond_at(41.0, "unknown", transient=True)

        self.at(50.999)
        self.assertEqual([], self.requests)
        self.at(51.0)
        self.assertEqual(1, len(self.requests))

    def test_throttling_waits_as_instructed(self):
        self.start()
        self.respond_at(1.0, "unknown", retry_after=120.0)

        self.at(120.9)
        self.assertEqual([], self.requests)
        self.at(121.0)
        self.assertEqual(1, len(self.requests))

    def test_throttling_beyond_the_deadline_abandons(self):
        self.start()
        self.respond_at(1.0, "unknown", retry_after=ci_status.DEADLINE)

        self.assertEqual("", self.text())
        self.assertEqual({}, ci_status.runs)

    def test_terminal_unknown_stops_quietly(self):
        self.start()
        self.respond_at(1.0, "unknown", reason="commit not found")

        self.assertEqual("", self.text())
        self.assertEqual({}, ci_status.runs)

    def test_overall_deadline(self):
        self.start()
        t = 0.0
        while t + 10.0 < ci_status.DEADLINE:
            self.respond_at(t, "pending")
            t += 10.0
            self.at(t)

        self.at(ci_status.DEADLINE)

        self.assertEqual("", self.text())
        self.assertEqual({}, ci_status.runs)


class TestRunIdentity(CiWatcherTestCase):
    def test_new_run_for_same_key_supersedes_old_responses(self):
        self.start()
        old_run, _ = self.requests.pop(0)
        self.at(5.0)
        self.start()

        ci_status.on_observation(old_run, CiObservation("failure"))

        self.assertNotIn(self.text(), ("[CI: failed]", "[red]"))
        self.assertEqual("[CI: ◇]", self.text())

    def test_runs_for_other_keys_publish_to_their_own_keys(self):
        other_key = ("feature", OTHER_OID)
        self.start()
        self.start(key=other_key)

        self.respond_at(1.0, "failure")

        self.assertEqual("[CI: failed]", self.text())
        self.assertEqual("[CI: ◇]", self.text(other_key))

    def test_returning_to_a_settled_key_does_not_replay_transitions(self):
        self.start()
        self.respond_at(1.0, "failure")
        self.at(7.0)

        self.at(8.0)

        self.assertEqual("[red]", self.text())
        self.assertEqual([], self.requests)

    def test_changed_integration_source_clears_cache_and_invalidates_runs(self):
        self.start()
        old_run, _ = self.requests.pop(0)
        self.start(key=("other", OTHER_OID))
        self.requests.clear()
        elsewhere = GitHubRepo("", "github.com", "someone", "else", "secret")

        self.start(key=("third", OID), repository=elsewhere, url="git@github.com:someone/else")
        ci_status.on_observation(old_run, CiObservation("failure"))

        cache = store.current_state(REPO)["github_ci_status"]
        self.assertEqual([("third", OID)], list(cache.keys()))

    def test_same_source_keeps_cache(self):
        self.start()
        self.start(key=("other", OTHER_OID), url="https://github.com/UPSTREAM/project")

        self.assertEqual({KEY, ("other", OTHER_OID)}, set(store.current_state(REPO)["github_ci_status"]))


class TestRemoteInfoUpdates(CiWatcherTestCase):
    def setUp(self):
        super().setUp()
        when(ci_status).enqueue_on_ui(...).thenAnswer(lambda fn, *args: fn(*args))
        self.start()

    def publish_remote_info(self, remotes, integration_remote):
        blob = RemoteInfoBlob(
            [RemoteInfo(name, url, [], url) for name, url in remotes.items()], None, integration_remote
        )
        ci_status.on_remote_info_update(REPO, {"remote_info": blob})

    @p.expand([
        ("renamed remote", {"up": UPSTREAM_URL}, "up"),
        ("same repository, other url", {"origin": "https://github.com/upstream/project.git"}, "origin"),
    ])
    def test_same_repository_preserves_state(self, _, remotes, integration_remote):
        self.publish_remote_info(remotes, integration_remote)

        self.assertIn((REPO, KEY), ci_status.runs)
        self.assertEqual("[CI: ◇]", self.text())

    @p.expand([
        ("other repository", {"origin": "git@github.com:other/project.git"}, "origin"),
        ("no integration remote", {}, None),
    ])
    def test_other_source_resets(self, _, remotes, integration_remote):
        self.publish_remote_info(remotes, integration_remote)

        self.assertEqual({}, ci_status.runs)
        self.assertEqual(0, len(store.current_state(REPO)["github_ci_status"]))


class TestEnablement(CiWatcherTestCase):
    def test_disabling_cancels_runs_and_clears_results(self):
        self.start()
        run, _ = self.requests.pop(0)
        when(ci_status).is_enabled().thenReturn(False)

        ci_status.on_settings_changed()
        ci_status.on_observation(run, CiObservation("failure"))

        self.assertEqual({}, ci_status.runs)
        self.assertEqual(0, len(store.current_state(REPO)["github_ci_status"]))

    def test_reenabling_makes_no_request(self):
        when(ci_status).is_enabled().thenReturn(False)
        ci_status.on_settings_changed()
        when(ci_status).is_enabled().thenReturn(True)

        ci_status.on_settings_changed()

        self.assertEqual([], self.requests)

    def test_disabled_start_run_does_nothing(self):
        when(ci_status).is_enabled().thenReturn(False)

        self.start()

        self.assertEqual([], self.requests)
        self.assertIsNone(self.text())

    def test_stop_invalidates_outstanding_callbacks(self):
        self.start()
        run, _ = self.requests.pop(0)

        try:
            ci_status.stop()
            ci_status.on_observation(run, CiObservation("failure"))
        finally:
            ci_status.start()

        self.assertEqual({}, ci_status.runs)
        self.assertEqual("[CI: ◇]", self.text())


class TestPushCapture(DeferrableTestCase):
    def setUp(self):
        self.cmd = mock({"repo_path": REPO})
        self.remotes = {
            "origin": "git@github.com:me/project.git",
            "upstream": "https://github.com/upstream/project.git",
        }
        self.remote_config = {}
        self.integration_remote = "upstream"
        when(self.cmd).get_remote_info().thenAnswer(lambda: RemoteInfoBlob(
            [
                RemoteInfo(name, url, self.remote_config.get(name, []), url)
                for name, url in self.remotes.items()
            ],
            "origin",
            self.integration_remote
        ))
        when(ci_status).is_enabled().thenReturn(True)
        when(ci_status).enqueue_on_ui(...).thenAnswer(lambda fn, *args: fn(*args))
        when(ci_status).start_run(...).thenReturn(None)
        when(github.app_settings).get("api_tokens", {}).thenReturn({"github.com": "secret"})
        when(github.app_settings).get("github_enterprise_hosts").thenReturn([])

    def tearDown(self):
        unstub()

    def push(self, output=None, sha=OID):
        output = output or porcelain("git@github.com:me/project.git", "*", "feature:refs/heads/feature")
        ci_status.on_successful_push(self.cmd, "origin", parse_push_porcelain(output), "feature", sha)

    def test_queries_integration_repository_and_verifies_fork(self):
        self.push()

        verify(ci_status).start_run(
            REPO,
            ("feature", OID),
            GitHubRepo("https://github.com/upstream/project", "github.com", "upstream", "project", "secret"),
            PushTarget("me", "project", "refs/heads/feature"),
            "https://github.com/upstream/project.git"
        )
        verify(self.cmd, times=1).get_remote_info()

    def test_renamed_destination_and_force_push(self):
        self.push(porcelain("https://github.com/me/project", "+", "feature:refs/heads/renamed"))

        verify(ci_status).start_run(
            REPO, ("feature", OID), ANY(), PushTarget("me", "project", "refs/heads/renamed"), ANY()
        )

    def test_integration_uses_the_effective_url(self):
        when(self.cmd).get_remote_info().thenReturn(RemoteInfoBlob(
            [
                RemoteInfo("origin", "git@github.com:me/project.git", [], "gh:me/project"),
                RemoteInfo("upstream", "https://github.com/upstream/project", [], "gh:upstream/project"),
            ],
            "origin",
            "upstream"
        ))

        self.push()

        verify(ci_status).start_run(
            REPO, ("feature", OID),
            GitHubRepo("https://github.com/upstream/project", "github.com", "upstream", "project", "secret"),
            ANY(), "https://github.com/upstream/project"
        )

    def test_ssh_alias_of_the_push_url_is_resolved(self):
        when(github)._read_ssh_config_hostname("ci-status-alias").thenReturn("github.com")

        self.push(porcelain("ci-status-alias:me/project.git", " ", "feature:refs/heads/feature"))

        verify(ci_status).start_run(REPO, ANY(), ANY(), PushTarget("me", "project", "refs/heads/feature"), ...)

    def test_configured_enterprise_host(self):
        when(github.app_settings).get("api_tokens", {}).thenReturn({"github.example.com": "ghe"})
        when(github.app_settings).get("github_enterprise_hosts").thenReturn(["github.example.com"])
        self.remotes["upstream"] = "https://github.example.com/upstream/project"

        self.push(porcelain("https://github.example.com/me/project", "*", "feature:refs/heads/feature"))

        verify(ci_status).start_run(
            REPO, ANY(),
            GitHubRepo("https://github.example.com/upstream/project", "github.example.com",
                       "upstream", "project", "ghe"),
            ANY(), ANY()
        )

    @p.expand([
        ("no sha", dict(sha=None)),
        ("deleted branch", dict(output="To https://github.com/me/project\n-\t:refs/heads/feature\t[deleted]\nDone\n")),
        ("rejected", dict(
            output="To https://github.com/me/project\n!\tfeature:refs/heads/feature\t[rejected]\nDone\n"
        )),
        ("tag", dict(output="To https://github.com/me/project\n*\tv1:refs/tags/v1\t[new tag]\nDone\n")),
        ("two destinations", dict(output=(
            "To https://github.com/me/project\n*\tfeature:refs/heads/feature\t[new branch]\nDone\n"
            "To https://github.com/me/backup\n*\tfeature:refs/heads/feature\t[new branch]\nDone\n"
        ))),
        ("two refs", dict(output=(
            "To https://github.com/me/project\n*\tfeature:refs/heads/feature\t[new branch]\n"
            "*\tother:refs/heads/other\t[new branch]\nDone\n"
        ))),
        ("other host", dict(output="To https://gitlab.com/me/project\n*\tfeature:refs/heads/feature\t[new]\nDone\n")),
        ("local path", dict(output="To /tmp/project.git\n*\tfeature:refs/heads/feature\t[new]\nDone\n")),
    ])
    def test_unsupported_pushes_skip_monitoring(self, _, kwargs):
        self.push(**kwargs)

        verify(ci_status, times=0).start_run(...)

    @p.expand([("true",), ("Yes",), ("",)])
    def test_mirror_remote_skips(self, value):
        self.remote_config["origin"] = [ConfigEntry("mirror", value)]

        self.push()

        verify(ci_status, times=0).start_run(...)

    def test_mirror_on_another_remote_does_not_skip(self):
        self.remote_config["upstream"] = [ConfigEntry("mirror", "true")]
        self.remote_config["origin"] = [ConfigEntry("mirror", "false")]

        self.push()

        verify(ci_status).start_run(...)

    @p.expand([
        ("gitlab", "https://gitlab.com/upstream/project"),
        ("unlisted enterprise host", "https://github.example.com/upstream/project"),
        ("lookalike", "https://github.com.example.com/upstream/project"),
    ])
    def test_non_github_integration_hosts_skip(self, _, url):
        when(github.app_settings).get("api_tokens", {}).thenReturn({
            "gitlab.com": "t", "github.example.com": "t", "github.com.example.com": "t"
        })
        self.remotes["upstream"] = url

        self.push()

        verify(ci_status, times=0).start_run(...)

    def test_missing_token_skips(self):
        when(github.app_settings).get("api_tokens", {}).thenReturn({})
        when(github.os.environ).get("GITHUB_TOKEN").thenReturn(None)

        self.push()

        verify(ci_status, times=0).start_run(...)

    def test_disabled_reads_nothing(self):
        when(ci_status).is_enabled().thenReturn(False)

        self.push()

        verify(self.cmd, times=0).get_remote_info()
        verify(ci_status, times=0).start_run(...)


class TestSingleBranchUpdate(DeferrableTestCase):
    @p.expand([
        (" ", ("https://github.com/me/p", "refs/heads/main")),
        ("+", ("https://github.com/me/p", "refs/heads/main")),
        ("*", ("https://github.com/me/p", "refs/heads/main")),
        ("=", ("https://github.com/me/p", "refs/heads/main")),
        ("!", None),
        ("-", None),
    ])
    def test_flags(self, flag, expected):
        pushed = [PushDestination("https://github.com/me/p", [
            PushedRef(flag, "refs/heads/main", "refs/heads/main", "[summary]")
        ])]

        self.assertEqual(expected, ci_status.single_branch_update(pushed))

    def test_nothing_pushed(self):
        self.assertIsNone(ci_status.single_branch_update([]))
        self.assertIsNone(ci_status.single_branch_update([PushDestination("https://github.com/me/p", [])]))


def porcelain(url, flag, refs):
    return "To {}\n{}\t{}\t[summary]\nDone\n".format(url, flag, refs)
