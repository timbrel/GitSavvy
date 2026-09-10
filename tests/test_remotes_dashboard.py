from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core.git_mixins.active_branch import Commit
from GitSavvy.core.git_mixins.remotes import ConfigEntry, RemoteInfo, RemoteInfoBlob
from GitSavvy.core.interfaces.remotes import RemotesInterface
from GitSavvy.core.types import ShortHash


class TestRemotesDashboard(DeferrableTestCase):
    def test_single_remote_with_config_has_one_blank_line_before_help(self) -> None:
        output = render_dashboard([
            remote("origin", "https://example.com/project.git", [
                ConfigEntry("gh-resolved", "base")
            ])
        ])

        self.assertEqual(output, """
  ROOT:    /repo

  BRANCH:  On branch `main`.
  HEAD:    1234567 A commit

  REMOTE:
    origin  https://example.com/project.git
      gh-resolved = base

  #############
""")

    def test_detailed_remotes_have_one_blank_line_between_each_remote(self) -> None:
        output = render_dashboard([
            remote("origin", "https://example.com/origin.git", [
                ConfigEntry("fetch", "^refs/heads/main")
            ]),
            remote("upstream", "https://example.com/upstream.git", [
                ConfigEntry("push", "+refs/heads/*:refs/heads/*"),
                ConfigEntry("tagopt", "--no-tags")
            ])
        ], integration_remote="origin")

        self.assertEqual(output, """
  ROOT:    /repo

  BRANCH:  On branch `main`.
  HEAD:    1234567 A commit

  REMOTE:
  * origin    https://example.com/origin.git
      fetch = ^refs/heads/main

    upstream  https://example.com/upstream.git
      push = +refs/heads/*:refs/heads/*
      tagopt = --no-tags

  #############
""")

    def test_compact_remote_list_has_no_blank_lines_between_remotes(self) -> None:
        output = render_dashboard([
            remote("origin", "https://example.com/origin.git"),
            remote("fork", "https://example.com/fork.git"),
            remote("backup", "https://example.com/backup.git")
        ], push_remote="fork", integration_remote="origin")

        self.assertEqual(output, """
  ROOT:    /repo

  BRANCH:  On branch `main`.
  HEAD:    1234567 A commit

  REMOTE:
  * origin  https://example.com/origin.git
  ▸ fork    https://example.com/fork.git
    backup  https://example.com/backup.git

  #############
""")


def render_dashboard(
    remotes: list[RemoteInfo],
    *,
    push_remote: str | None = None,
    integration_remote: str | None = None
) -> str:
    interface = RemotesInterface.__new__(RemotesInterface)
    interface.template_help = "\n  #############"
    interface.state = {
        "git_root": "/repo",
        "long_status": "On branch `main`.",
        "recent_commits": [Commit(ShortHash("1234567"), "", "A commit")],
        "remote_info": RemoteInfoBlob(remotes, push_remote, integration_remote),
        "show_help": True
    }
    output, _regions = interface._render_template()
    return output


def remote(
    name: str,
    url: str,
    config: list[ConfigEntry] | None = None
) -> RemoteInfo:
    return RemoteInfo(name, url, config or [])
