from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core.caches import Cache
from GitSavvy.core.commands import status_bar
from GitSavvy.core.git_mixins.status import HeadState
from GitSavvy.core.types import FullHash
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


OID = FullHash("a" * 40)
OTHER_OID = FullHash("b" * 40)


class TestUpdateStatus(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    @p.expand([(False,), (True,)])
    def test_pending_requests_for_one_repo_are_coalesced(self, different_views):
        pending = []
        when(status_bar).run_when_worker_is_idle(...).thenAnswer(pending.append)
        view = mock()
        commands = [
            status_bar.gs_update_status(view),
            status_bar.gs_update_status(mock() if different_views else view),
        ]
        calls = []
        for command in commands:
            when(command).find_repo_path().thenReturn('/repo')
            when(command).update_working_dir_status().thenAnswer(lambda: calls.append('status'))
            command.run(None)

        for task in pending:
            task()

        self.assertEqual(['status'], calls)

    def test_only_latest_request_resolves_its_repository_on_worker(self):
        pending = []
        when(status_bar).run_when_worker_is_idle(...).thenAnswer(pending.append)
        commands = [status_bar.gs_update_status(mock()) for _ in range(2)]
        for command, repo in zip(commands, ['/repo', '/other']):
            when(command).find_repo_path().thenReturn(repo)
            when(command).update_working_dir_status().thenReturn(None)
            command.run(None)

        for command in commands:
            verify(command, times=0).find_repo_path()

        for task in pending:
            task()

        verify(commands[0], times=0).find_repo_path()
        verify(commands[0], times=0).update_working_dir_status()
        verify(commands[1], times=1).find_repo_path()
        verify(commands[1]).update_working_dir_status()

    def test_request_during_refresh_is_not_lost(self):
        pending = []
        when(status_bar).run_when_worker_is_idle(...).thenAnswer(pending.append)
        first = status_bar.gs_update_status(mock())
        second = status_bar.gs_update_status(mock())
        for command in (first, second):
            when(command).find_repo_path().thenReturn('/repo')
        when(first).update_working_dir_status().thenAnswer(lambda: second.run(None))
        when(second).update_working_dir_status().thenReturn(None)

        first.run(None)
        pending.pop(0)()
        self.assertEqual(1, len(pending))
        pending.pop(0)()

        verify(second).update_working_dir_status()

    def test_view_without_repository_does_not_query_status(self):
        pending = []
        command = status_bar.gs_update_status(mock())
        when(command).find_repo_path().thenReturn(None)
        when(status_bar).run_when_worker_is_idle(...).thenAnswer(pending.append)

        command.run(None)
        pending.pop()()

        verify(command, times=0).update_working_dir_status()


class TestComposeStatus(DeferrableTestCase):
    def setUp(self):
        self.settings = {"git_status_in_status_bar": True, "github_ci_status_in_status_bar": True}
        when(status_bar.app_settings).get(...).thenAnswer(
            lambda name, default=None: self.settings.get(name, default)
        )

    def tearDown(self):
        unstub()

    @p.expand([
        (True, True, "master+3 [CI: ◇]"),
        (True, False, "master+3"),
        (False, True, "[CI: ◇]"),
        (False, False, ""),
    ])
    def test_visibility_combinations(self, git_enabled, ci_enabled, expected):
        self.settings.update({
            "git_status_in_status_bar": git_enabled,
            "github_ci_status_in_status_bar": ci_enabled,
        })

        actual = status_bar.compose_status(state(ci={("master", OID): "[CI: ◇]"}))

        self.assertEqual(expected, actual)

    @p.expand([("",), ("[CI: ok]",)])
    def test_no_extra_spaces(self, ci_text):
        actual = status_bar.compose_status(state(short_status="", ci={("master", OID): ci_text}))

        self.assertEqual(ci_text, actual)

    def test_missing_short_status_still_renders_ci(self):
        s = state(ci={("master", OID): "[red]"})
        del s["short_status"]

        self.assertEqual("[red]", status_bar.compose_status(s))

    def test_ci_is_enabled_by_default(self):
        del self.settings["github_ci_status_in_status_bar"]

        actual = status_bar.compose_status(state(ci={("master", OID): "[red]"}))

        self.assertEqual("master+3 [red]", actual)


class TestSelectCiStatus(DeferrableTestCase):
    def test_attached_head_selects_branch_and_commit(self):
        ci = {("master", OID): "[red]", ("other", OID): "[CI: ok]"}

        self.assertEqual("[red]", status_bar.select_ci_status(state(ci=ci)))

    @p.expand([
        ("other commit", {("master", OTHER_OID): "[red]"}),
        ("other branch at same commit", {("feature", OID): "[red]"}),
        ("detached lookup of the same commit", {("", OID): "[red]"}),
        ("empty cache", {}),
    ])
    def test_attached_cache_miss_selects_nothing(self, _, ci):
        self.assertEqual("", status_bar.select_ci_status(state(ci=ci)))

    def test_detached_head_reuses_any_branch_with_that_commit(self):
        ci = {("feature", OID): "[red]", ("master", OTHER_OID): "[CI: ok]"}

        self.assertEqual("[red]", status_bar.select_ci_status(state(head=detached(OID), ci=ci)))

    def test_detached_head_selects_its_own_lookup(self):
        ci = {("", OID): "[red]", ("", OTHER_OID): ""}

        self.assertEqual("[red]", status_bar.select_ci_status(state(head=detached(OID), ci=ci)))

    def test_detached_head_prefers_the_most_recent_observation(self):
        ci = {("feature", OID): "[red]", ("master", OID): "[◈]"}

        self.assertEqual("[◈]", status_bar.select_ci_status(state(head=detached(OID), ci=ci)))

    @p.expand([
        ("no head", lambda: None),
        ("unborn branch", lambda: attached("master", None)),
    ])
    def test_missing_identity_selects_nothing(self, _, head):
        head = head()
        self.assertEqual("", status_bar.select_ci_status(state(head=head, ci={("master", OID): "[red]"})))

    def test_lookup_does_not_reorder_the_lru_cache(self):
        s = state(ci={("master", OID): "[red]", ("other", OID): ""})

        status_bar.select_ci_status(s)

        self.assertEqual([("master", OID), ("other", OID)], list(s["github_ci_status"]))


class TestOnStatusUpdate(DeferrableTestCase):
    def setUp(self):
        self.view = mock()
        window = mock()
        when(window).active_view().thenReturn(self.view)
        when(status_bar.sublime).active_window().thenReturn(window)

    def tearDown(self):
        unstub()

    def test_redraws_the_active_view_of_the_updated_repo(self):
        when(status_bar).find_repo_path_for_view(self.view).thenReturn("/repo")

        status_bar.on_status_update("/repo", {})

        verify(self.view).run_command("gs_draw_status_bar")

    @p.expand([("other repo", "/other"), ("no repo", None)])
    def test_skips_the_active_view_of_another_repo(self, _, view_repo_path):
        when(status_bar).find_repo_path_for_view(self.view).thenReturn(view_repo_path)

        status_bar.on_status_update("/repo", {})

        verify(self.view, times=0).run_command(...)


def attached(branch, commit_hash=OID):
    return HeadState(False, branch, commit_hash, None, True, "3", None, False)


def detached(commit_hash):
    return HeadState(True, None, commit_hash, None, True, None, None, False)


_unset = object()


def state(short_status="master+3", head=_unset, ci={}):
    cache = Cache(maxsize=64)
    for key, text in ci.items():
        cache[key] = text
    return {
        "short_status": short_status,
        "head": attached("master") if head is _unset else head,
        "github_ci_status": cache,
    }
