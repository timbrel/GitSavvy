import unittest

from GitSavvy.core.git_mixins.tags import TagDetails
from GitSavvy.core.interfaces.tags import (
    is_short_version_tag,
    remote_tag_conflict_message,
    tag_with_date,
    tags_that_already_exist_on_remote,
)


class TestTags(unittest.TestCase):
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
