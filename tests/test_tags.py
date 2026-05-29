import unittest

from GitSavvy.core.git_mixins.tags import TagDetails, TagsMixin, is_semver_tag
from GitSavvy.core.interfaces.tags import (
    is_short_version_tag,
    remote_tag_conflict_message,
    tag_with_date,
    tags_that_already_exist_on_remote,
)


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

    def test_get_remote_tags_skips_peeled_annotated_tags(self):
        tag_list = RemoteTagsRepo("""\
1111111111111111111111111111111111111111\trefs/tags/v1.2.3
2222222222222222222222222222222222222222\trefs/tags/v1.2.3^{}
3333333333333333333333333333333333333333\trefs/tags/v1.2.4
""").get_remote_tags("origin")
        self.assertEqual([tag.tag for tag in tag_list.versions], ["v1.2.4", "v1.2.3"])

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
