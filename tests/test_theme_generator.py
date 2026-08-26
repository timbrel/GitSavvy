from __future__ import annotations

import os
import tempfile

from unittesting import DeferrableTestCase

from GitSavvy.common import theme_generator
from GitSavvy.common.theme_generator import ColorRef, ScopedStyle
from GitSavvy.tests.mockito import unstub, when


class TestThemeGenerator(DeferrableTestCase):
    def setUp(self) -> None:
        theme_generator.stop_watchers()
        self.provided_styles = theme_generator._provided_styles.copy()
        theme_generator._provided_styles.clear()

    def tearDown(self) -> None:
        theme_generator.stop_watchers()
        theme_generator._provided_styles.clear()
        theme_generator._provided_styles.update(self.provided_styles)
        unstub()

    def test_start_only_primes_watchers(self) -> None:
        preferences = WatchableSettings({
            "color_scheme": "Packages/Example/Example.sublime-color-scheme"
        })
        syntax_settings = WatchableSettings({})
        app_settings = WatchableSettings({"colors": {}})
        settings = {
            "Preferences.sublime-settings": preferences,
            "GitSavvy.sublime-settings": app_settings,
            "graph.sublime-settings": syntax_settings,
        }
        queued = []
        when(theme_generator.sublime).load_settings(...).thenAnswer(
            lambda name: settings[name]
        )
        when(theme_generator).enqueue_on_ui(...).thenAnswer(
            lambda callback, *args: queued.append((callback, args))
        )
        theme_generator.provide("graph", [marker_style()])

        theme_generator.start_watchers()

        self.assertEqual(len(theme_generator._settings_watchers), 3)
        self.assertEqual(
            theme_generator._settings_watchers[
                "Preferences.sublime-settings"
            ].values,
            (
                "Packages/Example/Example.sublime-color-scheme",
                None,
                None,
            )
        )
        self.assertEqual(queued, [])

        preferences.set("font_size", 14)
        self.assertEqual(queued, [])

        preferences.set("color_scheme", "Packages/Example/Other.sublime-color-scheme")
        self.assertEqual(len(queued), 1)

    def test_provide_replaces_styles_and_schedules_refresh_after_start(self) -> None:
        settings = WatchableSettings({})
        when(theme_generator.sublime).load_settings(...).thenReturn(settings)
        queued = []
        when(theme_generator).enqueue_on_ui(...).thenAnswer(
            lambda callback, *args: queued.append((callback, args))
        )
        theme_generator.start_watchers()
        queued.clear()

        style = marker_style()
        theme_generator.provide("graph", [style])

        self.assertEqual(theme_generator._provided_styles["graph"], (style,))
        self.assertIn(
            "graph.sublime-settings",
            theme_generator._settings_watchers
        )
        self.assertEqual(len(queued), 1)

        theme_generator.provide("graph", [style])
        self.assertEqual(len(queued), 1)

    def test_source_scheme_comes_from_syntax_settings(self) -> None:
        when(theme_generator.sublime).load_settings(
            "Preferences.sublime-settings"
        ).thenReturn({
            "color_scheme": "Packages/Example/Global.sublime-color-scheme",
        })
        when(theme_generator.sublime).load_settings(
            "graph.sublime-settings"
        ).thenReturn({
            "color_scheme": "Packages/Example/Syntax.sublime-color-scheme",
        })

        schemes = theme_generator.effective_color_schemes("graph")

        self.assertEqual(schemes, ["Syntax.sublime-color-scheme"])

    def test_auto_scheme_falls_back_to_preferences_for_each_mode(self) -> None:
        when(theme_generator.sublime).load_settings(
            "Preferences.sublime-settings"
        ).thenReturn({
            "color_scheme": "auto",
            "dark_color_scheme": "Packages/Example/Dark.sublime-color-scheme",
        })
        when(theme_generator.sublime).load_settings(
            "graph.sublime-settings"
        ).thenReturn({
            "light_color_scheme": "Packages/Example/Light.tmTheme",
        })

        schemes = theme_generator.effective_color_schemes("graph")

        self.assertEqual(schemes, [
            "Light.sublime-color-scheme",
            "Dark.sublime-color-scheme",
        ])

    def test_scheme_names_are_normalized_to_the_modern_extension(self) -> None:
        self.assertEqual(
            theme_generator.normalize_scheme_name(
                "Packages/Example/One Dark (SL).tmTheme"
            ),
            "One Dark (SL).sublime-color-scheme"
        )
        self.assertEqual(
            theme_generator.normalize_scheme_name(
                "Packages/Example/Selenized (black).sublime-color-scheme"
            ),
            "Selenized (black).sublime-color-scheme"
        )
        self.assertIsNone(
            theme_generator.normalize_scheme_name("Packages/Example/Selenized")
        )

    def test_rendered_scheme_contains_all_resolved_styles(self) -> None:
        dashboard_style = ScopedStyle(
            "Dashboard Marker",
            "git_savvy.dashboard.multiselect",
            foreground=ColorRef("dashboard", "multiselect_background")
        )
        theme_generator.provide("status", [dashboard_style])
        theme_generator.provide("tags", [dashboard_style])
        theme_generator.provide("graph", [marker_style()])
        when(theme_generator).color_value(
            "test", "marker"
        ).thenReturn("#abc")
        when(theme_generator).color_value(
            "dashboard", "multiselect_background"
        ).thenReturn("#def")

        contents = theme_generator.render_scheme()
        scheme = theme_generator.sublime.decode_value(contents)

        self.assertTrue(contents.startswith(theme_generator.DOCUMENTATION_HEADER))
        self.assertEqual(scheme["variables"], {})
        self.assertEqual(scheme["globals"], {})
        self.assertEqual(scheme["rules"], [
            {
                "name": "Dashboard Marker",
                "scope": "git_savvy.dashboard.multiselect",
                "foreground": "#def",
            },
            {
                "name": "Marker",
                "scope": "git-savvy.graph git_savvy.marker",
                "foreground": "#abc",
            },
        ])

    def test_synchronizes_the_active_filename_set(self) -> None:
        contents = "first contents\n"
        updated_contents = "updated contents\n"
        records = []
        when(theme_generator.app_state).get(
            theme_generator.GENERATED_SCHEMES_KEY
        ).thenAnswer(lambda key: records[-1] if records else None)
        when(theme_generator.app_state).set(
            theme_generator.GENERATED_SCHEMES_KEY, ...
        ).thenAnswer(lambda key, value: records.append(value))
        with tempfile.TemporaryDirectory() as directory:
            when(theme_generator.sublime).packages_path().thenReturn(directory)
            output_dir = os.path.join(directory, "User", "GitSavvy")
            os.makedirs(output_dir)
            stale = os.path.join(output_dir, "Unused.sublime-color-scheme")
            legacy = os.path.join(
                output_dir,
                "GitSavvy.status.color_scheme.sublime-color-scheme"
            )
            unrelated = os.path.join(output_dir, "notes.txt")
            for path in (stale, legacy, unrelated):
                with open(path, "w", encoding="utf-8") as file:
                    file.write("keep?")

            filenames = {"Selenized (black).sublime-color-scheme"}
            theme_generator.synchronize_color_schemes(filenames, contents)
            generated = os.path.join(output_dir, next(iter(filenames)))
            initial_stat = os.stat(generated)

            self.assertFalse(os.path.exists(stale))
            self.assertTrue(os.path.exists(legacy))
            self.assertTrue(os.path.exists(unrelated))
            with open(generated, encoding="utf-8") as file:
                self.assertEqual(file.read(), contents)

            theme_generator.synchronize_color_schemes(filenames, contents)
            self.assertEqual(os.stat(generated).st_mtime_ns, initial_stat.st_mtime_ns)

            second_filename = "One Dark.sublime-color-scheme"
            filenames.add(second_filename)
            theme_generator.synchronize_color_schemes(filenames, contents)
            self.assertEqual(os.stat(generated).st_mtime_ns, initial_stat.st_mtime_ns)
            self.assertTrue(os.path.exists(os.path.join(output_dir, second_filename)))

            theme_generator.synchronize_color_schemes(filenames, updated_contents)
            with open(generated, encoding="utf-8") as file:
                self.assertEqual(file.read(), updated_contents)

    def test_refresh_writes_identical_contents_to_all_effective_schemes(self) -> None:
        theme_generator.provide("graph", [marker_style()])
        theme_generator.provide("status", [])
        when(theme_generator).effective_color_schemes("graph").thenReturn([
            "Graph.sublime-color-scheme"
        ])
        when(theme_generator).effective_color_schemes("status").thenReturn([
            "Status.sublime-color-scheme"
        ])
        when(theme_generator).color_value("test", "marker").thenReturn("#abc")
        captured = []
        when(theme_generator).synchronize_color_schemes(...).thenAnswer(
            lambda filenames, contents: captured.append((filenames, contents))
        )

        theme_generator.refresh()

        self.assertEqual(captured, [(
            {
                "Graph.sublime-color-scheme",
                "Status.sublime-color-scheme",
            },
            theme_generator.render_scheme(),
        )])

    def test_migrates_legacy_overrides_only_for_provided_syntaxes(self) -> None:
        theme_generator.provide("graph", [marker_style()])
        view = FakeView({
            "syntax": "Packages/GitSavvy/syntax/graph.sublime-syntax",
            "color_scheme": (
                "Packages/User/GitSavvy/"
                "GitSavvy.graph.color_scheme.hidden-color-scheme"
            ),
            "dark_color_scheme": "GitSavvy.graph.dark_color_scheme.hidden-tmTheme",
            "light_color_scheme": "Packages/Example/Light.sublime-color-scheme",
        })

        theme_generator.migrate_color_scheme(view)

        self.assertNotIn("color_scheme", view.settings())
        self.assertNotIn("dark_color_scheme", view.settings())
        self.assertEqual(
            view.settings()["light_color_scheme"],
            "Packages/Example/Light.sublime-color-scheme"
        )

        ordinary_view = FakeView({
            "syntax": "Packages/Python/Python.sublime-syntax",
            "color_scheme": "GitSavvy.custom.sublime-color-scheme",
        })
        theme_generator.migrate_color_scheme(ordinary_view)
        self.assertIn("color_scheme", ordinary_view.settings())


class FakeView:
    def __init__(self, settings: dict) -> None:
        self._settings = FakeViewSettings(settings)

    def settings(self) -> "FakeViewSettings":
        return self._settings


class FakeViewSettings(dict):
    def erase(self, key: str) -> None:
        self.pop(key, None)


class WatchableSettings(dict):
    def __init__(self, values: dict) -> None:
        super().__init__(values)
        self.callbacks = {}

    def add_on_change(self, key: str, callback) -> None:
        self.callbacks[key] = callback

    def clear_on_change(self, key: str) -> None:
        self.callbacks.pop(key, None)

    def set(self, key: str, value: object) -> None:
        self[key] = value
        for callback in list(self.callbacks.values()):
            callback()


def marker_style() -> ScopedStyle:
    return ScopedStyle(
        "Marker",
        "git-savvy.graph git_savvy.marker",
        foreground=ColorRef("test", "marker")
    )
