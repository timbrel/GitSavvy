from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.tests.mockito import expect, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p
from GitSavvy.core.git_command import GitCommand
from GitSavvy.core.git_mixins.remotes import (
    ConfigEntry,
    PushDestination,
    PushedRef,
    RemoteInfo,
    RemoteInfoBlob,
    parse_push_porcelain,
    rewrite_url
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
                ], "https://github.com/packagecontrol/st_package_reviewer.git"),
                RemoteInfo("kaste", "https://github.com/kaste/st_package_reviewer.git", [
                    ConfigEntry("url", "https://github.com/kaste/st_package_reviewer.git"),
                    ConfigEntry("fetch", "+refs/heads/*:refs/remotes/kaste/*"),
                ], "https://github.com/kaste/st_package_reviewer.git"),
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
                    ],
                    "https://github.com/michaelblyons/SublimeText-PC-Package-Reviewer.git"
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

    def test_get_remote_info_resolves_insteadof(self) -> None:
        repo = GitCommand()
        when(repo).git("config", "--get-regexp", ...).thenReturn(
            "url.git@github.com:.insteadof gh:\n"
            "remote.origin.url gh:kaste/GitSavvy\n"
            "remote.other.url https://example.com/other.git\n"
        )
        when(repo).current_state().thenReturn({})
        expect(repo).update_store(...)

        remotes = repo.get_remote_info().remotes

        self.assertEqual([
            ("origin", "git@github.com:kaste/GitSavvy", "gh:kaste/GitSavvy"),
            ("other", "https://example.com/other.git", "https://example.com/other.git"),
        ], [(r.name, r.url, r.configured_url) for r in remotes])
        verify(repo).update_store({
            "remotes": {
                "origin": "git@github.com:kaste/GitSavvy",
                "other": "https://example.com/other.git",
            }
        })


class TestRewriteUrl(DeferrableTestCase):
    @p.expand([
        ("rewrites the prefix", "gh:me/p", [
            ("url.https://github.com/.insteadof", "gh:")
        ], "https://github.com/me/p"),
        ("longest prefix wins", "gh:me/p", [
            ("url.https://a/.insteadof", "gh:"),
            ("url.https://github.com/x/.insteadof", "gh:me/"),
        ], "https://github.com/x/p"),
        ("no matching prefix", "https://github.com/me/p", [
            ("url.https://a/.insteadof", "gh:")
        ], "https://github.com/me/p"),
        ("prefix is case-sensitive", "GH:me/p", [
            ("url.https://github.com/.insteadof", "gh:")
        ], "GH:me/p"),
        ("base keeps its case", "gh:me/p", [
            ("url.https://GitHub.com/.insteadof", "gh:")
        ], "https://GitHub.com/me/p"),
        ("other config is ignored", "https://github.com/me/p", [
            ("remote.origin.url", "https:")
        ], "https://github.com/me/p"),
    ])
    def test_rewrite_url(self, _, url, entries, expected) -> None:
        config_entries = [ConfigEntry(key, value) for key, value in entries]

        self.assertEqual(expected, rewrite_url(url, config_entries))


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


# Captured from `git push --porcelain origin main :gone v1 main:main2`
PUSH_OUTPUT = (
    "To ../r.git\n"
    "-\t:refs/heads/gone\t[deleted]\n"
    " \trefs/heads/main:refs/heads/main\tdf749db..eade9cb\n"
    "*\trefs/tags/v1:refs/tags/v1\t[new tag]\n"
    "*\trefs/heads/main:refs/heads/main2\t[new branch]\n"
    "Done\n"
)


class TestPush(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    def test_returns_the_parsed_porcelain_report(self) -> None:
        repo = GitCommand()
        when(repo).git(...).thenReturn(PUSH_OUTPUT)

        self.assertEqual(repo.push("origin", "main", remote_branch="main2"), [
            PushDestination("../r.git", [
                PushedRef("-", "", "refs/heads/gone", "[deleted]"),
                PushedRef(" ", "refs/heads/main", "refs/heads/main", "df749db..eade9cb"),
                PushedRef("*", "refs/tags/v1", "refs/tags/v1", "[new tag]"),
                PushedRef("*", "refs/heads/main", "refs/heads/main2", "[new branch]"),
            ])
        ])
        verify(repo).git("push", "--porcelain", None, None, None, "origin", "main:main2")

    def test_parses_one_entry_per_destination(self) -> None:
        output = (
            "To https://example.com/a.git\n"
            "=\trefs/heads/main:refs/heads/main\t[up to date]\n"
            "Done\n"
            "To https://example.com/b.git\n"
            "!\trefs/heads/main:refs/heads/main\t[rejected] (non-fast-forward)\n"
            "Done\n"
        )

        self.assertEqual(parse_push_porcelain(output), [
            PushDestination("https://example.com/a.git", [
                PushedRef("=", "refs/heads/main", "refs/heads/main", "[up to date]"),
            ]),
            PushDestination("https://example.com/b.git", [
                PushedRef("!", "refs/heads/main", "refs/heads/main", "[rejected] (non-fast-forward)"),
            ]),
        ])

    def test_ignores_lines_outside_of_destinations(self) -> None:
        self.assertEqual(parse_push_porcelain(""), [])
        self.assertEqual(parse_push_porcelain(" \trefs/heads/a:refs/heads/a\tx\n"), [])
