from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core.commands import pull
from GitSavvy.tests.mockito import mock, unstub, verify, when


class TestPullCurrentCheckout(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    def test_no_upstream_still_opens_pull_from_branch(self) -> None:
        command = pull.gs_pull(mock())
        when(command).get_upstream_for_active_branch().thenReturn(None)
        when(command.window).run_command("gs_pull_from_branch", {"rebase": True}).thenReturn(None)

        pull.gs_pull.run.__wrapped__(command, rebase=True)

        verify(command.window).run_command("gs_pull_from_branch", {"rebase": True})

    def test_upstream_still_uses_normal_pull(self) -> None:
        command = pull.gs_pull(mock())
        when(command).get_upstream_for_active_branch().thenReturn(mock({
            "remote": "origin", "branch": "feature"
        }))
        when(command).do_pull("origin", "feature", None).thenReturn(None)

        pull.gs_pull.run.__wrapped__(command)

        verify(command).do_pull("origin", "feature", None)
