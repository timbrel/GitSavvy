from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core.commands import push
from GitSavvy.core.exceptions import GitSavvyError
from GitSavvy.core.git_mixins.remotes import PushDestination, PushedRef
from GitSavvy.core.git_mixins.status import HeadState
from GitSavvy.core.types import FullHash
from GitSavvy.github.commands.pull_request import gs_github_push_and_create_pull_request
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


OID = FullHash("a" * 40)
HEAD_OID = FullHash("c" * 40)
PUSHED = [PushDestination("https://github.com/me/project", [
    PushedRef("*", "refs/heads/feature", "refs/heads/feature", "[new branch]")
])]


class TestPushStartsCiObservation(DeferrableTestCase):
    def setUp(self):
        self.window = mock()
        when(self.window).status_message(...).thenReturn(None)
        when(self.window).run_command(...).thenReturn(None)
        when(push.interface_util).refresh_gitsavvy_interfaces(...).thenReturn(None)
        when(push.ci_status).on_successful_push(...).thenReturn(None)

    def tearDown(self):
        unstub()

    def command(self, cls=push.gs_push_to_branch_name):
        cmd = cls(self.window)
        when(cmd).push(...).thenReturn(PUSHED)
        when(cmd).current_state().thenReturn({
            "head": HeadState(False, "feature", HEAD_OID, None, True, None, None, False)
        })
        return cmd

    def test_successful_push_hands_the_branch_snapshot_to_the_watcher(self):
        cmd = self.command()

        cmd.do_push("origin", "feature", remote_branch="renamed", local_branch_sha=OID)

        verify(push.ci_status).on_successful_push(cmd, "origin", PUSHED, "feature", OID)

    @p.expand([
        ("checked out branch", "feature", HEAD_OID),
        ("other branch", "other", None),
    ])
    def test_without_snapshot_falls_back_to_the_head_state(self, _, branch, expected_sha):
        cmd = self.command()

        cmd.do_push("origin", branch)

        verify(push.ci_status).on_successful_push(cmd, "origin", PUSHED, branch, expected_sha)

    def test_failed_push_starts_no_observation(self):
        cmd = self.command()
        when(cmd).push(...).thenRaise(GitSavvyError("rejected", show_panel=False))

        with self.assertRaises(GitSavvyError):
            cmd.do_push("origin", "feature", local_branch_sha=OID)

        verify(push.ci_status, times=0).on_successful_push(...)

    def test_watcher_failure_does_not_fail_the_push(self):
        cmd = self.command()
        when(push.ci_status).on_successful_push(...).thenRaise(RuntimeError("boom"))

        cmd.do_push("origin", "feature", local_branch_sha=OID)

        verify(self.window).status_message(push.END_PUSH_MESSAGE)

    def test_watcher_failure_does_not_suppress_pull_request_creation(self):
        cmd = self.command(gs_github_push_and_create_pull_request)
        when(push.ci_status).on_successful_push(...).thenRaise(RuntimeError("boom"))

        cmd.do_push("origin", "feature", local_branch_sha=OID)

        verify(self.window).run_command("gs_github_create_pull_request")

    def test_current_branch_provider_passes_its_commit(self):
        cmd = self.command()
        branch = mock({"name": "feature", "commit_hash": OID})
        when(cmd).get_current_branch().thenReturn(branch)
        received = []

        push.take_current_branch_name(cmd, {}, lambda value, **kw: received.append((value, kw)))

        self.assertEqual([("feature", {"local_branch_sha": OID})], received)
