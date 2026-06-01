from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.tests.mockito import expect, unstub, verify, when
from GitSavvy.core.git_command import GitCommand
from GitSavvy.core.git_mixins.remotes import (
    ConfigEntry,
    RemoteInfo,
    RemoteInfoBlob
)


CONFIG_OUTPUT = """\
gitsavvy.ghremote kaste
gitsavvy.pushdefault michaelblyons
remote.origin.url https://github.com/packagecontrol/st_package_reviewer.git
remote.origin.fetch +refs/heads/*:refs/remotes/origin/*
remote.kaste.url https://github.com/kaste/st_package_reviewer.git
remote.kaste.fetch +refs/heads/*:refs/remotes/kaste/*
remote.michaelblyons.url https://github.com/michaelblyons/SublimeText-PC-Package-Reviewer.git
remote.michaelblyons.fetch +refs/heads/*:refs/remotes/michaelblyons/*
remote.michaelblyons.push +refs/heads/*:refs/heads/*
remote.michaelblyons.tagopt --no-tags
remote.michaelblyons.followremotehead never
remote.michaelblyons.fetch ^refs/heads/main
"""


class TestRemoteConfigParsing(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    def test_read_config_entries_parses_git_config_output(self) -> None:
        repo = GitCommand()
        when(repo).git("config", "--get-regexp", ...).thenReturn(CONFIG_OUTPUT)
        when(repo).current_state().thenReturn({})

        self.assertEqual(repo.read_config_entries(), [
            ConfigEntry("gitsavvy.ghremote", "kaste"),
            ConfigEntry("gitsavvy.pushdefault", "michaelblyons"),
            ConfigEntry("remote.origin.url", "https://github.com/packagecontrol/st_package_reviewer.git"),
            ConfigEntry("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
            ConfigEntry("remote.kaste.url", "https://github.com/kaste/st_package_reviewer.git"),
            ConfigEntry("remote.kaste.fetch", "+refs/heads/*:refs/remotes/kaste/*"),
            ConfigEntry(
                "remote.michaelblyons.url",
                "https://github.com/michaelblyons/SublimeText-PC-Package-Reviewer.git"
            ),
            ConfigEntry("remote.michaelblyons.fetch", "+refs/heads/*:refs/remotes/michaelblyons/*"),
            ConfigEntry("remote.michaelblyons.push", "+refs/heads/*:refs/heads/*"),
            ConfigEntry("remote.michaelblyons.tagopt", "--no-tags"),
            ConfigEntry("remote.michaelblyons.followremotehead", "never"),
            ConfigEntry("remote.michaelblyons.fetch", "^refs/heads/main"),
        ])

    def test_get_remote_info_reads_config_once(self) -> None:
        repo = GitCommand()
        when(repo).git("config", "--get-regexp", ...).thenReturn(CONFIG_OUTPUT)
        when(repo).get_upstream_for_active_branch().thenRaise(
            AssertionError("Should not ask git for upstream when ghremote is configured."))
        when(repo).current_state().thenReturn({})
        expect(repo).update_store(...)

        self.assertEqual(repo.get_remote_info(), RemoteInfoBlob(
            remotes=[
                RemoteInfo("origin", "https://github.com/packagecontrol/st_package_reviewer.git", [
                    ConfigEntry("url", "https://github.com/packagecontrol/st_package_reviewer.git"),
                    ConfigEntry("fetch", "+refs/heads/*:refs/remotes/origin/*"),
                ]),
                RemoteInfo("kaste", "https://github.com/kaste/st_package_reviewer.git", [
                    ConfigEntry("url", "https://github.com/kaste/st_package_reviewer.git"),
                    ConfigEntry("fetch", "+refs/heads/*:refs/remotes/kaste/*"),
                ]),
                RemoteInfo(
                    "michaelblyons",
                    "https://github.com/michaelblyons/SublimeText-PC-Package-Reviewer.git",
                    [
                        ConfigEntry(
                            "url",
                            "https://github.com/michaelblyons/SublimeText-PC-Package-Reviewer.git"
                        ),
                        ConfigEntry("fetch", "+refs/heads/*:refs/remotes/michaelblyons/*"),
                        ConfigEntry("push", "+refs/heads/*:refs/heads/*"),
                        ConfigEntry("tagopt", "--no-tags"),
                        ConfigEntry("followremotehead", "never"),
                        ConfigEntry("fetch", "^refs/heads/main"),
                    ]
                ),
            ],
            push_remote="michaelblyons",
            integration_remote="kaste"
        ))
        verify(repo, times=1).git("config", "--get-regexp", ...)
        verify(repo).update_store({
            "remotes": {
                "origin": "https://github.com/packagecontrol/st_package_reviewer.git",
                "kaste": "https://github.com/kaste/st_package_reviewer.git",
                "michaelblyons": "https://github.com/michaelblyons/SublimeText-PC-Package-Reviewer.git",
            }
        })


class TestGuessRemoteToPushTo(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    def test_rejects_empty_remote_list(self) -> None:
        repo = GitCommand()

        with self.assertRaisesRegex(ValueError, "requires at least one remote"):
            repo.guess_remote_to_push_to([])

    def test_returns_the_only_remote(self) -> None:
        repo = GitCommand()

        self.assertEqual(repo.guess_remote_to_push_to(["origin"]), "origin")

    def test_last_remote_used_wins(self) -> None:
        repo = GitCommand()
        when(repo).current_state().thenReturn({"last_remote_used_for_push": "michaelblyons"})

        self.assertEqual(
            repo.guess_remote_to_push_to(
                ["origin", "michaelblyons"],
                [ConfigEntry("gitsavvy.pushdefault", "origin")]
            ),
            "michaelblyons"
        )

    def test_gitsavvy_pushdefault_wins_over_remote_pushdefault(self) -> None:
        repo = GitCommand()
        when(repo).current_state().thenReturn({})

        self.assertEqual(
            repo.guess_remote_to_push_to(
                ["origin", "fork", "michaelblyons"],
                [
                    ConfigEntry("remote.pushdefault", "fork"),
                    ConfigEntry("gitsavvy.pushdefault", "michaelblyons"),
                ]
            ),
            "michaelblyons"
        )

    def test_remote_pushdefault_wins_over_fallback_names(self) -> None:
        repo = GitCommand()
        when(repo).current_state().thenReturn({})

        self.assertEqual(
            repo.guess_remote_to_push_to(
                ["origin", "fork", "kaste"],
                [ConfigEntry("remote.pushdefault", "kaste")]
            ),
            "kaste"
        )

    def test_falls_back_to_fork_then_origin(self) -> None:
        repo = GitCommand()
        when(repo).current_state().thenReturn({})

        self.assertEqual(repo.guess_remote_to_push_to(["origin", "fork"], []), "fork")
        self.assertEqual(repo.guess_remote_to_push_to(["origin", "kaste"], []), "origin")

    def test_falls_back_to_first_remote(self) -> None:
        repo = GitCommand()
        when(repo).current_state().thenReturn({})

        self.assertEqual(repo.guess_remote_to_push_to(["kaste", "michaelblyons"], []), "kaste")

    def test_reads_config_entries_if_not_provided(self) -> None:
        repo = GitCommand()
        when(repo).current_state().thenReturn({})
        when(repo).git("config", "--get-regexp", ...).thenReturn(
            "gitsavvy.pushdefault michaelblyons"
        )

        self.assertEqual(repo.guess_remote_to_push_to(["origin", "michaelblyons"]), "michaelblyons")
        verify(repo, times=1).git("config", "--get-regexp", ...)
