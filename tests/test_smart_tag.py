from datetime import datetime
import unittest

from GitSavvy.core.commands.tag import (
    calendar_version,
    calendar_version_options,
    calendar_version_style_is_valid,
    default_tag_message,
    smart_incremented_tag,
)


class TestSmartTag(unittest.TestCase):
    def test_calendar_version(self):
        now = datetime(2026, 5, 29, 12, 34, 56)
        self.assertEqual(
            calendar_version("{year}.{month}.{day}.{hour}.{minute}.{second}", now),
            "2026.05.29.12.34.56"
        )

    def test_calendar_version_options(self):
        now = datetime(2026, 5, 29, 12, 34, 56)
        self.assertEqual(
            calendar_version_options("{year}.{month}.{day}", now),
            ("2026.05.29", "2026.05.29.12.34")
        )
        self.assertEqual(
            calendar_version_options("{year}.{month}.{day}.{hour}.{minute}", now),
            ("2026.05.29.12.34", "2026.05.29")
        )

    def test_calendar_version_style_is_valid_rejects_invalid_settings(self):
        invalid_settings = [None, "", "{year}.{unknown}", "{year.foo}", "{"]
        for setting in invalid_settings:
            with self.subTest(setting=setting):
                self.assertFalse(calendar_version_style_is_valid(setting))

    def test_default_tag_message(self):
        self.assertEqual(default_tag_message('v{tag_name}', '2.1.0'), 'v2.1.0')
        self.assertEqual(default_tag_message('v{tag_name}', 'v2.1.0'), 'v2.1.0')
        self.assertEqual(default_tag_message('V{tag_name}', 'v2.1.0'), 'v2.1.0')
        self.assertEqual(default_tag_message('Tag {tag_name}', 'v2.1.0'), 'Tag v2.1.0')
        self.assertEqual(default_tag_message('Release v{tag_name}', 'v2.1.0'), 'Release v2.1.0')

    def test_smart_tag(self):
        self.assertEqual(smart_incremented_tag('v1.3.2', "prerelease"), 'v1.3.3-0')
        self.assertEqual(smart_incremented_tag('v1.3.2', "prepatch"), 'v1.3.3-0')
        self.assertEqual(smart_incremented_tag('v1.3.2', "preminor"), 'v1.4.0-0')
        self.assertEqual(smart_incremented_tag('v1.3.2', "premajor"), 'v2.0.0-0')
        self.assertEqual(smart_incremented_tag('v1.3.2', "patch"), 'v1.3.3')
        self.assertEqual(smart_incremented_tag('v1.3.2', "minor"), 'v1.4.0')
        self.assertEqual(smart_incremented_tag('v1.3.2', "major"), 'v2.0.0')
        self.assertEqual(smart_incremented_tag('v1.3.2-1', "prerelease"), 'v1.3.2-2')
        self.assertEqual(smart_incremented_tag('v1.3.2-1', "prepatch"), 'v1.3.3-0')
        self.assertEqual(smart_incremented_tag('v1.3.2-1', "preminor"), 'v1.4.0-0')
        self.assertEqual(smart_incremented_tag('v1.3.2-1', "premajor"), 'v2.0.0-0')
        self.assertEqual(smart_incremented_tag('v1.3.2-1', "patch"), 'v1.3.2')
        self.assertEqual(smart_incremented_tag('v1.3.2-1', "minor"), 'v1.4.0')
        self.assertEqual(smart_incremented_tag('v1.3.2-1', "major"), 'v2.0.0')
        self.assertEqual(smart_incremented_tag('v1.3.0-1', "patch"), 'v1.3.0')
        self.assertEqual(smart_incremented_tag('v1.3.0-1', "minor"), 'v1.3.0')
        self.assertEqual(smart_incremented_tag('v1.3.0-1', "major"), 'v2.0.0')
        self.assertEqual(smart_incremented_tag('v1.0.0-1', "major"), 'v1.0.0')
        self.assertEqual(smart_incremented_tag('v1.3.2-rc.1', "prerelease"), 'v1.3.2-rc.2')
        self.assertEqual(smart_incremented_tag('v1.3.2-rc.11', "prerelease"), 'v1.3.2-rc.12')
        self.assertEqual(smart_incremented_tag('v1.3.2-beta1', "prerelease"), 'v1.3.2-beta2')
        self.assertEqual(smart_incremented_tag('v1.3.2-beta9', "prerelease"), 'v1.3.2-beta10')
        self.assertEqual(smart_incremented_tag('v1.3.2-rc.1', "prepatch"), 'v1.3.3-rc.0')
        self.assertEqual(smart_incremented_tag('v1.3.2-rc.1', "preminor"), 'v1.4.0-rc.0')
        self.assertEqual(smart_incremented_tag('v1.3.2-rc.1', "premajor"), 'v2.0.0-rc.0')
        self.assertEqual(smart_incremented_tag('v1.3.2-rc.1', "patch"), 'v1.3.2')
        self.assertEqual(smart_incremented_tag('v1.3.2-rc.1', "minor"), 'v1.4.0')
        self.assertEqual(smart_incremented_tag('v1.3.2-rc.1', "major"), 'v2.0.0')
        self.assertEqual(smart_incremented_tag('v1.3.0-rc.1', "patch"), 'v1.3.0')
        self.assertEqual(smart_incremented_tag('v1.3.0-rc.1', "minor"), 'v1.3.0')
        self.assertEqual(smart_incremented_tag('v1.3.0-rc.1', "major"), 'v2.0.0')
        self.assertEqual(smart_incremented_tag('v1.0.0-rc.1', "major"), 'v1.0.0')
