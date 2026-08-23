from functools import partial

from unittesting import DeferrableTestCase

from GitSavvy.core.settings import color_value, read_default_settings


class TestReadDefaultSettings(DeferrableTestCase):
    def test_reads_shipped_settings(self) -> None:
        settings = read_default_settings()

        self.assertEqual(
            settings["colors"]["log_graph"]["commit_dot_background"],
            "#991"
        )


class TestColorValue(DeferrableTestCase):
    def setUp(self) -> None:
        user_settings = {
            "log_graph": {
                "commit_dot_background": "#eee",
                "commit_dot_foreground": "",
            }
        }
        default_settings = {
            "log_graph": {
                "commit_dot_background": "#991",
                "commit_dot_foreground": "#9911",
                "path_background": "#99991109",
            }
        }
        self.color = partial(
            color_value,
            user_settings,
            default_settings,
            "log_graph"
        )

    def test_prefers_user_value(self) -> None:
        self.assertEqual(self.color("commit_dot_background"), "#eee")

    def test_falls_back_to_default_value(self) -> None:
        self.assertEqual(self.color("path_background"), "#99991109")

    def test_preserves_explicit_falsey_user_value(self) -> None:
        self.assertEqual(self.color("commit_dot_foreground"), "")
