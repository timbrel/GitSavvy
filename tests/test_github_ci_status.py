from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core.types import FullHash
from GitSavvy.github import github
from GitSavvy.tests.mockito import any as ANY, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


OID = FullHash("a" * 40)
OTHER_OID = FullHash("b" * 40)


class TestGitHubCiStatusRequest(DeferrableTestCase):
    def tearDown(self):
        unstub()

    @p.expand([
        ("github.com", "api.github.com", "/graphql"),
        ("GitHub.com", "api.github.com", "/graphql"),
        ("github.example.com", "github.example.com", "/api/graphql"),
    ])
    def test_posts_query_variables_with_bearer_token(self, host, api_host, path):
        repository = github.GitHubRepo("", host, "owner", "repo", "secret")
        when(github.interwebs).post(...).thenReturn(ci_response(ci_payload()))

        observation = github.query_ci_status(repository, OID)

        self.assertEqual(github.CiObservation("success"), observation)
        verify(github.interwebs).post(
            api_host, 443, path,
            payload={
                "query": github.CI_STATUS_QUERY,
                "variables": {"owner": "owner", "name": "repo", "oid": OID},
            },
            headers={"Authorization": "bearer secret", "Accept": "application/json"},
            timeout=github.CI_REQUEST_TIMEOUT
        )

    def test_first_request_verifies_pushed_ref_in_the_same_request(self):
        repository = github.GitHubRepo("", "github.com", "upstream", "repo", "secret")
        target = github.PushTarget("fork", "repo-fork", "refs/heads/feature")
        when(github.interwebs).post(...).thenReturn(ci_response(ci_payload(pushed_oid=OID)))

        observation = github.query_ci_status(repository, OID, target)

        self.assertEqual("success", observation.state)
        verify(github.interwebs).post(
            "api.github.com", 443, "/graphql",
            payload={
                "query": github.CI_STATUS_AND_PUSH_TIP_QUERY,
                "variables": {
                    "owner": "upstream", "name": "repo", "oid": OID,
                    "pushOwner": "fork", "pushName": "repo-fork",
                    "pushRef": "refs/heads/feature",
                },
            },
            headers=ANY(),
            timeout=ANY()
        )

    def test_missing_token_makes_no_request(self):
        repository = github.GitHubRepo("", "github.com", "owner", "repo", None)
        when(github.interwebs).post(...)

        observation = github.query_ci_status(repository, OID)

        self.assertEqual("unknown", observation.state)
        self.assertFalse(observation.transient)
        verify(github.interwebs, times=0).post(...)

    @p.expand([
        (OSError("boom"),),
        (TimeoutError(),),
        (github.http.client.BadStatusLine("x"),),
        (ValueError("bad json"),),
    ])
    def test_transport_errors_are_transient_unknowns(self, exc):
        repository = github.GitHubRepo("", "github.com", "owner", "repo", "secret")
        when(github.interwebs).post(...).thenRaise(exc)

        observation = github.query_ci_status(repository, OID)

        self.assertEqual("unknown", observation.state)
        self.assertTrue(observation.transient)
        self.assertNotIn("secret", observation.reason)

    @p.expand([
        ("github.com", [], True),
        ("GITHUB.COM", [], True),
        ("github.example.com", [], False),
        ("github.example.com", ["GitHub.Example.com"], True),
        ("evilgithub.com", [], False),
        ("sub.github.com", [], False),
        ("github.com.example.com", [], False),
        ("gitlab.com", ["github.example.com"], False),
    ])
    def test_github_host_recognition(self, host, enterprise_hosts, expected):
        when(github.app_settings).get("github_enterprise_hosts").thenReturn(enterprise_hosts)

        self.assertEqual(expected, github.is_github_host(host))


class TestGitHubCiStatusInterpretation(DeferrableTestCase):
    def tearDown(self):
        unstub()

    @p.expand([
        ("SUCCESS", 2, "success"),
        ("FAILURE", 1, "failure"),
        ("ERROR", 1, "failure"),
        ("PENDING", 3, "pending"),
        ("EXPECTED", 1, "pending"),
        (None, 0, "no_checks"),
        ("SUCCESS", 0, "no_checks"),
        ("PENDING", 0, "no_checks"),
        ("WHATEVER", 1, "unknown"),
    ])
    def test_rollup_states(self, state, count, expected):
        observation = github.interpret_ci_response(
            ci_response(ci_payload(state, count)), OID, verify_push_tip=False
        )
        self.assertEqual(expected, observation.state)

    @p.expand([
        ("missing commit", {"data": {"repository": {"object": None}}}),
        ("missing repository", {"data": {"repository": None}}),
        ("other commit", lambda: ci_payload(oid=OTHER_OID)),
        ("no data", {}),
        ("malformed count", {"data": {"repository": {"object": {
            "oid": OID, "statusCheckRollup": {"state": "SUCCESS", "contexts": {}}
        }}}}),
        ("boolean count", lambda: ci_payload(count=True)),
        ("graphql errors with data", lambda: {**ci_payload(), "errors": [{"type": "NOT_FOUND"}]}),
        ("malformed errors", lambda: {**ci_payload(), "errors": "nope"}),
        ("list payload", []),
    ])
    def test_partial_or_malformed_responses_are_terminal_unknowns(self, _, payload):
        payload = payload() if callable(payload) else payload

        observation = github.interpret_ci_response(ci_response(payload), OID, verify_push_tip=False)

        self.assertEqual("unknown", observation.state)
        self.assertFalse(observation.transient)
        self.assertIsNone(observation.retry_after)

    @p.expand([
        ("missing tip", lambda: ci_payload()),
        ("moved tip", lambda: ci_payload(pushed_oid=OTHER_OID)),
        ("inaccessible fork", lambda: {"data": {**ci_payload()["data"], "pushed": None}}),
    ])
    def test_unverified_push_tip_is_unknown(self, _, payload):
        observation = github.interpret_ci_response(ci_response(payload()), OID, verify_push_tip=True)

        self.assertEqual("unknown", observation.state)
        self.assertFalse(observation.transient)

    def test_verified_push_tip_reports_the_rollup(self):
        observation = github.interpret_ci_response(
            ci_response(ci_payload("FAILURE", pushed_oid=OID)), OID, verify_push_tip=True
        )
        self.assertEqual("failure", observation.state)

    @p.expand([
        (401, {"message": "Bad credentials"}),
        (403, {"message": "Resource not accessible"}),
        (404, {"message": "Not Found"}),
        (200, "<html>"),
    ])
    def test_http_failures_are_terminal_unknowns(self, status, payload):
        response = ci_response(payload, status, is_json=isinstance(payload, dict))

        observation = github.interpret_ci_response(response, OID, verify_push_tip=False)

        self.assertEqual("unknown", observation.state)
        self.assertFalse(observation.transient)
        self.assertIsNone(observation.retry_after)

    @p.expand([(500,), (502,), (503,)])
    def test_server_errors_are_transient(self, status):
        observation = github.interpret_ci_response(
            ci_response({"message": "oops"}, status), OID, verify_push_tip=False
        )
        self.assertEqual("unknown", observation.state)
        self.assertTrue(observation.transient)

    @p.expand([
        ("retry-after header", 403, {"retry-after": "42"}, {"message": "secondary"}, 42.0),
        ("429 without headers", 429, {}, {"message": "slow down"}, github.DEFAULT_THROTTLE_DELAY),
        ("403 rate limit message", 403, {}, {"message": "API rate limit exceeded"},
         github.DEFAULT_THROTTLE_DELAY),
        ("primary limit reset", 403,
         {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1100"}, {"message": "x"}, 100.0),
        ("graphql RATE_LIMITED", 200,
         {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"},
         {"errors": [{"type": "RATE_LIMITED"}]}, 30.0),
        ("reset in the past", 403,
         {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "900"}, {"message": "x"}, 0.0),
    ])
    def test_throttling_honors_github_headers(self, _, status, headers, payload, expected):
        when(github.time).time().thenReturn(1000.0)

        observation = github.interpret_ci_response(
            ci_response(payload, status, headers), OID, verify_push_tip=False
        )

        self.assertEqual("unknown", observation.state)
        self.assertEqual(expected, observation.retry_after)


class TestGitHubCiStatusesRequest(DeferrableTestCase):
    def tearDown(self):
        unstub()

    def test_queries_all_commits_in_one_request(self):
        repository = github.GitHubRepo("", "github.com", "owner", "repo", "secret")
        when(github.interwebs).post(...).thenReturn(ci_response(batch_payload(
            c0=commit(OID, "FAILURE"), c1=commit(OTHER_OID, "SUCCESS")
        )))

        observations = github.query_ci_statuses(repository, [OID, OTHER_OID, OID])

        self.assertEqual({
            OID: github.CiObservation("failure"),
            OTHER_OID: github.CiObservation("success"),
        }, observations)
        verify(github.interwebs, times=1).post(
            "api.github.com", 443, "/graphql",
            payload={
                "query": github.ci_statuses_query(2),
                "variables": {"owner": "owner", "name": "repo", "c0": OID, "c1": OTHER_OID},
            },
            headers={"Authorization": "bearer secret", "Accept": "application/json"},
            timeout=github.CI_REQUEST_TIMEOUT
        )

    def test_query_aliases_each_commit(self):
        query = github.ci_statuses_query(2)

        self.assertIn("$c0: GitObjectID!, $c1: GitObjectID!", query)
        self.assertIn("c0: object(oid: $c0) { ...Rollup }", query)
        self.assertIn("c1: object(oid: $c1) { ...Rollup }", query)
        self.assertNotIn("$c2", query)
        self.assertIn("fragment Rollup on Commit", query)

    def test_unknown_commits_are_unknown_on_their_own(self):
        repository = github.GitHubRepo("", "github.com", "owner", "repo", "secret")
        when(github.interwebs).post(...).thenReturn(ci_response(batch_payload(
            c0=None, c1=commit(OTHER_OID, None)
        )))

        observations = github.query_ci_statuses(repository, [OID, OTHER_OID])

        self.assertEqual("unknown", observations[OID].state)
        self.assertEqual("no_checks", observations[OTHER_OID].state)

    @p.expand([
        ("graphql errors", lambda: ci_response({**batch_payload(c0=None, c1=None), "errors": [{}]})),
        ("server error", lambda: ci_response({"message": "oops"}, 502)),
        ("missing repository", lambda: ci_response({"data": {"repository": None}})),
    ])
    def test_failed_requests_fail_every_commit(self, _, response):
        repository = github.GitHubRepo("", "github.com", "owner", "repo", "secret")
        when(github.interwebs).post(...).thenReturn(response())

        observations = github.query_ci_statuses(repository, [OID, OTHER_OID])

        self.assertEqual([OID, OTHER_OID], list(observations))
        self.assertEqual({"unknown"}, {o.state for o in observations.values()})
        self.assertEqual(1, len(set(observations.values())))

    def test_transport_errors_are_transient_for_every_commit(self):
        repository = github.GitHubRepo("", "github.com", "owner", "repo", "secret")
        when(github.interwebs).post(...).thenRaise(TimeoutError())

        observations = github.query_ci_statuses(repository, [OID, OTHER_OID])

        self.assertTrue(all(o.state == "unknown" and o.transient for o in observations.values()))

    @p.expand([
        ("no commits", "secret", [], {}),
        ("no token", None, [OID], {OID: github.CiObservation("unknown", "no token")}),
    ])
    def test_makes_no_request(self, _, token, oids, expected):
        repository = github.GitHubRepo("", "github.com", "owner", "repo", token)
        when(github.interwebs).post(...)

        self.assertEqual(expected, github.query_ci_statuses(repository, oids))
        verify(github.interwebs, times=0).post(...)


def batch_payload(**commits):
    return {"data": {"repository": commits}}


def commit(oid, state="SUCCESS", count=1):
    return {
        "oid": oid,
        "statusCheckRollup": (
            None if state is None
            else {"state": state, "contexts": {"totalCount": count}}
        )
    }


def ci_payload(state="SUCCESS", count=1, oid=OID, pushed_oid=None):
    data = {
        "repository": {
            "object": {
                "oid": oid,
                "statusCheckRollup": (
                    None if state is None
                    else {"state": state, "contexts": {"totalCount": count}}
                )
            }
        }
    }
    if pushed_oid is not None:
        data["pushed"] = {"ref": {"target": {"oid": pushed_oid}}}
    return {"data": data}


def ci_response(payload, status=200, headers={}, is_json=True):
    return github.interwebs.Response(payload, dict(headers), status, is_json)
