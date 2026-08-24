from unittesting import DeferrableTestCase

from GitSavvy.core import settings
from GitSavvy.core.settings import color_value, read_default_settings
from GitSavvy.tests.mockito import unstub, when


class TestReadDefaultSettings(DeferrableTestCase):
    def test_reads_shipped_settings(self) -> None:
        settings = read_default_settings()

        self.assertEqual(
            settings["colors"]["log_graph"]["commit_dot_background"],
            "#991"
        )


class TestColorValue(DeferrableTestCase):
    def setUp(self) -> None:
        app_settings = {
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

        when(settings).get_global_settings().thenReturn({"colors": app_settings})
        when(settings).read_default_settings().thenReturn({"colors": default_settings})

    def tearDown(self) -> None:
        unstub()

    def test_prefers_app_value(self) -> None:
        self.assertEqual(
            color_value("log_graph", "commit_dot_background"),
            "#eee"
        )

    def test_falls_back_to_default_value(self) -> None:
        self.assertEqual(
            color_value("log_graph", "path_background"),
            "#99991109"
        )

    def test_preserves_explicit_falsey_app_value(self) -> None:
        self.assertEqual(
            color_value("log_graph", "commit_dot_foreground"),
            ""
        )
