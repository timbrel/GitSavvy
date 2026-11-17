import unittest

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p
from GitSavvy.core.git_mixins.tags import TagDetails, TagsMixin, is_semver_tag, is_version_tag
from GitSavvy.core.interfaces.tags import (
    gs_tags_show_graph,
    is_short_version_tag,
    remote_tag_conflict_message,
    tag_with_date,
    tags_that_already_exist_on_remote,
)


class TestTagsShowGraph(DeferrableTestCase):
    def setUp(self):
        self.addCleanup(unstub)
        self.view = sublime.active_window().new_file()
        self.view.set_scratch(True)
        self.addCleanup(self.view.close)
        self.view.assign_syntax("Packages/GitSavvy/syntax/tags.sublime-syntax")
        self.view.run_command("append", {"characters": (
            "  LOCAL:\n    aaaaaaa v1.2.3\n"
            "  REMOTE (origin):\n    1111111 v1.2.3\n    3333333 v1.2.4\n"
        )})
        remote_start = self.view.find("  REMOTE", 0).begin()
        self.view.add_regions("git_savvy_interface.local_tags", [sublime.Region(0, remote_start)])
        self.view.add_regions("git_savvy_interface.remote_tags_list_origin", [
            sublime.Region(remote_start, self.view.size())
        ])
        self.command = gs_tags_show_graph(self.view)
        self.window = mock()
        self.command.window = self.window
        when(self.window).run_command(...).thenReturn(None)
        when(self.command).get_short_hash_length().thenReturn(7)
        when(self.command).git(...).thenRaise(AssertionError("Unexpected Git query"))
        remote_tags = RemoteTagsRepo(
            f"{'1' * 40}\trefs/tags/v1.2.3\n"
            f"{'2' * 40}\trefs/tags/v1.2.3^{{}}\n"
            f"{'3' * 40}\trefs/tags/v1.2.4\n"
        ).get_remote_tags("origin")
        self.command.interface = mock({"state": {
            "remotes": {"origin": "unused"},
            "remote_tags_info": {
                "origin": {"state": "succeeded", "tags": list(remote_tags.all)},
                "loading": {"state": "loading"},
                "failed": {"state": "erred", "message": "unavailable"}
            }
        }})

    @p.expand([
        ("aaaaaaa", "tag: v1.2.3"),
        ("1111111", "2222222"),
        ("3333333", "3333333"),
    ])
    def test_follows_selected_tag(self, selected, follow):
        yield lambda: self.view.find_by_selector("meta.git-savvy.tag.name")
        self.view.sel().clear()
        self.view.sel().add(self.view.find(selected, 0).begin())

        self.command.run(None)

        verify(self.window).run_command("gs_graph", {"all": True, "follow": follow})
        verify(self.command, times=0).git(...)

    def test_no_tag_selected_does_not_open_graph(self):
        self.view.sel().clear()
        self.view.sel().add(0)
        self.command.run(None)
        verify(self.window, times=0).run_command(...)


class RemoteTagsRepo(TagsMixin):
    def __init__(self, stdout):
        self.stdout = stdout

    def git_throwing_silently(self, *args, **kwargs):
        return self.stdout


class TestTags(unittest.TestCase):
    def test_is_semver_tag(self):
        self.assertTrue(is_semver_tag("1.2.3"))
        self.assertTrue(is_semver_tag("v1.2.3"))
        self.assertFalse(is_semver_tag("release-1.2.3"))
        self.assertFalse(is_semver_tag("2026.05.29"))
        self.assertFalse(is_semver_tag("v2026.05.29"))

    def test_is_version_tag(self):
        self.assertTrue(is_version_tag("v1.2.3"))
        self.assertTrue(is_version_tag("2026.05.29"))
        self.assertTrue(is_version_tag("2026.05.29.12.34.56"))
        self.assertFalse(is_version_tag("v1"))
        self.assertFalse(is_version_tag("private"))

    def test_get_remote_tags_skips_peeled_annotated_tags(self):
        tag_list = RemoteTagsRepo("""\
1111111111111111111111111111111111111111\trefs/tags/v1.2.3
2222222222222222222222222222222222222222\trefs/tags/v1.2.3^{}
3333333333333333333333333333333333333333\trefs/tags/v1.2.4
""").get_remote_tags("origin")
        self.assertEqual([tag.tag for tag in tag_list.versions], ["v1.2.4", "v1.2.3"])
        lightweight, annotated = tag_list.versions
        self.assertEqual(annotated.sha, "1" * 40)
        self.assertEqual(annotated.peeled, "2" * 40)
        self.assertEqual(lightweight.sha, "3" * 40)
        self.assertIsNone(lightweight.peeled)

    def test_handle_tags_prefers_semver(self):
        tag_list = TagsMixin().handle_semver_tags([
            TagDetails("abc", "2026.05.29", "", ""),
            TagDetails("def", "v1.2.3", "", ""),
            TagDetails("ghi", "private", "", "")
        ])
        self.assertEqual(tag_list.version_style, "semver")
        self.assertEqual([tag.tag for tag in tag_list.versions], ["v1.2.3"])
        self.assertEqual([tag.tag for tag in tag_list.regular], ["2026.05.29", "private"])
        self.assertEqual(
            [tag.tag for tag in tag_list.all],
            ["2026.05.29", "private", "v1.2.3"]
        )

    def test_handle_tags_sorts_semver_naturally(self):
        tag_list = TagsMixin().handle_semver_tags([
            TagDetails("abc", "v2.0.0", "", ""),
            TagDetails("def", "v10.0.0", "", ""),
            TagDetails("ghi", "v10.0.0-rc.1", "", "")
        ])
        self.assertEqual(
            [tag.tag for tag in tag_list.versions],
            ["v10.0.0", "v10.0.0-rc.1", "v2.0.0"]
        )

    def test_handle_tags_detects_calendar_versions(self):
        tag_list = TagsMixin().handle_semver_tags([
            TagDetails("abc", "2026.05.29", "", ""),
            TagDetails("def", "2026.05.29.12.34", "", ""),
            TagDetails("ghi", "private", "", "")
        ])
        self.assertEqual(tag_list.version_style, "calendar")
        self.assertEqual(
            [tag.tag for tag in tag_list.versions],
            ["2026.05.29.12.34", "2026.05.29"]
        )
        self.assertEqual([tag.tag for tag in tag_list.regular], ["private"])

    def test_is_short_version_tag(self):
        self.assertTrue(is_short_version_tag("v1"))
        self.assertTrue(is_short_version_tag("v1.2"))
        self.assertFalse(is_short_version_tag("1"))
        self.assertFalse(is_short_version_tag("v1.2.3"))
        self.assertFalse(is_short_version_tag("version-1"))

    def test_tag_with_date(self):
        tag = TagDetails("abc", "v1", "25 May 2026", "4 minutes ago")
        self.assertEqual(tag_with_date(tag, True), "v1         25 May 2026 (4 minutes ago)")
        self.assertEqual(tag_with_date(tag, False), "v1")

    def test_remote_tag_conflict_message(self):
        self.assertEqual(
            remote_tag_conflict_message(["a", "b", "c"]),
            "Abort, 'a', 'b', 'c' already exist on the remote."
        )

    def test_extract_tag_that_already_exists_on_remote(self):
        stderr = """\
To https://github.com/kaste/st_package_reviewer.git
 ! [rejected]        v1 -> v1 (already exists)
error: failed to push some refs to 'https://github.com/kaste/st_package_reviewer.git'
hint: Updates were rejected because the tag already exists in the remote.
"""
        self.assertEqual(tags_that_already_exist_on_remote(stderr, ["v1"]), ["v1"])

    def test_extract_multiple_tags_that_already_exist_on_remote(self):
        stderr = """\
To https://github.com/kaste/st_package_reviewer.git
 ! [rejected]        v1 -> v1 (already exists)
 ! [rejected]        v2 -> v2 (already exists)
error: failed to push some refs to 'https://github.com/kaste/st_package_reviewer.git'
"""
        self.assertEqual(tags_that_already_exist_on_remote(stderr, ["v1", "v2", "v3"]), ["v1", "v2"])

    def test_extract_tags_that_already_exist_on_remote_ignores_hint(self):
        stderr = "hint: Updates were rejected because the tag already exists in the remote."
        self.assertEqual(tags_that_already_exist_on_remote(stderr, ["v1"]), [])

    def test_extract_tag_that_already_exists_on_remote_from_full_ref(self):
        stderr = " ! [rejected]        refs/tags/v1 -> v1 (already exists)"
        self.assertEqual(tags_that_already_exist_on_remote(stderr, ["v1"]), ["v1"])

    def test_extract_tag_that_already_exists_on_remote_with_multiple_pushed_tags(self):
        stderr = """\
To https://github.com/kaste/st_package_reviewer.git
 ! [rejected]        v1 -> v1 (already exists)
error: failed to push some refs to 'https://github.com/kaste/st_package_reviewer.git'
hint: Updates were rejected because the tag already exists in the remote.
"""
        self.assertEqual(tags_that_already_exist_on_remote(stderr, ["v1", "v2"]), ["v1"])
