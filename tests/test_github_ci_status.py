from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.github import github
from GitSavvy.tests.mockito import any as ANY, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


OID = "a" * 40
OTHER_OID = "b" * 40


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
