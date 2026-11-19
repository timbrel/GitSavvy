from unittesting import DeferrableTestCase

from GitSavvy.core import settings
from GitSavvy.core.settings import (
    ProjectFileChanges,
    ProjectSettings,
    app_settings,
    color_value,
    read_default_settings,
)
from GitSavvy.tests.mockito import mock, unstub, verify, when


class TestReadDefaultSettings(DeferrableTestCase):
    def test_reads_shipped_settings(self) -> None:
        settings = read_default_settings()

        self.assertEqual(
            settings["colors"]["log_graph"]["commit_dot_background"],
            "#991"
        )


class TestAppSettingsSnapshot(DeferrableTestCase):
    def test_bulk_read_populates_cache_until_invalidated(self):
        self.addCleanup(unstub)
        backing = mock()
        when(settings.sublime).load_settings(...).thenReturn(backing)
        app = settings._AppSettings()
        when(backing).get("first").thenReturn(1)
        when(backing).to_dict().thenReturn({"first": 1, "other": 2})

        self.assertEqual(app.get("first"), 1)
        snapshot = app.snapshot()
        self.assertEqual(app.get("other"), 2)
        self.assertEqual(app.get("absent", 3), 3)
        self.assertEqual(app.snapshot(), {"first": 1, "other": 2})
        verify(backing, times=1).to_dict()
        verify(backing, times=0).get("other")
        verify(backing, times=0).get("absent")

        app._on_update()
        when(backing).to_dict().thenReturn({"other": 4})
        self.assertEqual(app.snapshot(), {"other": 4})
        self.assertEqual(snapshot, {"first": 1, "other": 2})


class TestProjectSettings(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    def test_returns_one_instance_per_window(self) -> None:
        window = Window({})

        self.assertIs(ProjectSettings(window), ProjectSettings(window))
        self.assertIsNot(ProjectSettings(window), ProjectSettings(Window({})))

    def test_prefers_project_value(self) -> None:
        window = Window({"settings": {"GitSavvy": {"git_path": "project-git"}}})

        self.assertEqual(ProjectSettings(window).get("git_path"), "project-git")

    def test_falls_back_to_app_value(self) -> None:
        window = Window({})
        when(app_settings).get("git_path", None).thenReturn("app-git")

        self.assertEqual(ProjectSettings(window).get("git_path"), "app-git")

    def test_forgets_instance_after_window_closes(self) -> None:
        window = Window({})
        project_settings = ProjectSettings(window)
        queued = []
        when(settings.sublime).set_timeout(...).thenAnswer(queued.append)

        ProjectFileChanges().on_pre_close_window(window)

        self.assertIs(ProjectSettings(window), project_settings)
        queued.pop()()
        self.assertIsNot(ProjectSettings(window), project_settings)


class TestColorValue(DeferrableTestCase):
    def setUp(self) -> None:
        app_colors = {
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

        when(app_settings).get("colors", {}).thenReturn(app_colors)
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


class Window:
    # Stay clear of the ids of real windows, `ProjectSettings` caches by id.
    next_id = 1_000_000

    def __init__(self, project_data: dict) -> None:
        self._project_data = project_data
        self._id = self.next_id
        Window.next_id += 1

    def id(self) -> int:
        return self._id

    def project_data(self) -> dict:
        return self._project_data
