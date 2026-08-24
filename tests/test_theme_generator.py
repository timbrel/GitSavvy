from __future__ import annotations
import os
import tempfile

from unittesting import DeferrableTestCase

from GitSavvy.common import theme_generator
from GitSavvy.common.theme_generator import ColorRef, ThemeGenerator
from GitSavvy.tests.mockito import ANY, unstub, verify, when


class TestThemeGenerator(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    def test_source_scheme_comes_from_syntax_settings(self) -> None:
        syntax_scheme = "Packages/Example/Syntax.sublime-color-scheme"
        when(theme_generator.sublime).load_settings(
            "Preferences.sublime-settings"
        ).thenReturn({
            "color_scheme": "Packages/Example/Global.sublime-color-scheme",
        })
        when(theme_generator.sublime).load_settings(
            "graph.sublime-settings"
        ).thenReturn({"color_scheme": syntax_scheme})

        generator = ThemeGenerator.for_view(FakeView({
            "syntax": "Packages/GitSavvy/syntax/graph.sublime-syntax",
            "color_scheme": "Packages/User/GitSavvy/generated.hidden-color-scheme",
        }))

        self.assertEqual(
            generator._resolved_schemes(),
            [(syntax_scheme, "color_scheme")]
        )

    def test_source_scheme_is_resolved_for_each_run(self) -> None:
        first = "Packages/Example/First.sublime-color-scheme"
        second = "Packages/Example/Second.sublime-color-scheme"
        when(theme_generator.sublime).load_settings(
            "Preferences.sublime-settings"
        ).thenReturn(
            {"color_scheme": first},
            {"color_scheme": second}
        )
        when(theme_generator.sublime).load_settings(
            "graph.sublime-settings"
        ).thenReturn({})
        generator = ThemeGenerator.for_view(FakeView({
            "syntax": "Packages/GitSavvy/syntax/graph.sublime-syntax",
        }))

        self.assertEqual(generator._resolved_schemes(), [(first, "color_scheme")])
        self.assertEqual(generator._resolved_schemes(), [(second, "color_scheme")])

    def test_auto_scheme_falls_back_to_global_preferences_by_key(self) -> None:
        light_scheme = "Packages/Example/Light.sublime-color-scheme"
        dark_scheme = "Packages/Example/Dark.sublime-color-scheme"
        when(theme_generator.sublime).load_settings(
            "Preferences.sublime-settings"
        ).thenReturn({
            "color_scheme": "auto",
            "dark_color_scheme": dark_scheme,
        })
        when(theme_generator.sublime).load_settings(
            "graph.sublime-settings"
        ).thenReturn({
            "light_color_scheme": light_scheme,
        })

        generator = ThemeGenerator.for_view(FakeView({
            "syntax": "Packages/GitSavvy/syntax/graph.sublime-syntax",
        }))

        self.assertEqual(generator._resolved_schemes(), [
            (light_scheme, "light_color_scheme"),
            (dark_scheme, "dark_color_scheme"),
        ])

    def test_switching_to_auto_removes_old_generated_settings(self) -> None:
        when(theme_generator.sublime).load_settings(
            "Preferences.sublime-settings"
        ).thenReturn({"color_scheme": "auto"})
        when(theme_generator.sublime).load_settings(
            "graph.sublime-settings"
        ).thenReturn({})
        view = FakeView({
            "syntax": "Packages/GitSavvy/syntax/graph.sublime-syntax",
            "color_scheme": generated_theme("color_scheme"),
            "light_color_scheme": generated_theme("light_color_scheme"),
            "dark_color_scheme": generated_theme("dark_color_scheme"),
        })

        ThemeGenerator.for_view(view).ensure_theme()

        self.assertNotIn("color_scheme", view.settings())
        self.assertNotIn("light_color_scheme", view.settings())
        self.assertNotIn("dark_color_scheme", view.settings())

    def test_native_scheme_removes_old_generated_setting(self) -> None:
        source = "Packages/Example/Example.sublime-color-scheme"
        generator = configured_generator(source, {
            "color_scheme": generated_theme("color_scheme"),
        })
        version = dependency_version(generator, source)
        filename = "GitSavvy.graph.color_scheme.hidden-color-scheme"
        when(theme_generator).theme_version_record(filename).thenReturn({
            "version": version,
            "generated": False,
        })

        generator.ensure_theme()

        self.assertNotIn("color_scheme", generator._view.settings())

    def test_cache_hit_does_not_materialize_scheme(self) -> None:
        source = "Packages/Example/Example.sublime-color-scheme"
        generator = configured_generator(source)
        version = dependency_version(generator, source)
        filename = "GitSavvy.graph.color_scheme.hidden-color-scheme"

        when(theme_generator).theme_version_record(filename).thenReturn({
            "version": version,
            "generated": False,
        })
        when(theme_generator).generator_for_scheme(...).thenRaise(
            AssertionError("cache hit must not load the scheme")
        )

        generator.ensure_theme()

    def test_generated_scheme_can_be_ensured_again(self) -> None:
        source = "Packages/Example/Example.sublime-color-scheme"
        generated = generated_theme("color_scheme")
        generator = configured_generator(source, {"color_scheme": generated})
        version = dependency_version(generator, source)
        filename = "GitSavvy.graph.color_scheme.hidden-color-scheme"
        when(theme_generator).theme_version_record(filename).thenReturn({
            "version": version,
            "generated": True,
        })
        when(theme_generator).generator_for_scheme(...).thenRaise(
            AssertionError("cache hit must not load the scheme")
        )
        when(theme_generator).try_apply_theme(
            generator._view, "color_scheme", generated
        ).thenAnswer(
            lambda view, setting_name, theme: view.settings().__setitem__(setting_name, theme)
        )

        with tempfile.TemporaryDirectory() as directory:
            when(theme_generator.sublime).packages_path().thenReturn(directory)
            path = os.path.join(directory, "User", "GitSavvy", filename)
            os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8") as file:
                file.write("{}")

            generator.ensure_theme()

        self.assertEqual(generator._view.settings()["color_scheme"], generated)

    def test_cache_miss_materializes_and_records_scheme(self) -> None:
        source = "Packages/Example/Example.sublime-color-scheme"
        generator = configured_generator(source)
        concrete_generator = FakeConcreteGenerator()
        filename = "GitSavvy.graph.color_scheme.hidden-color-scheme"

        when(theme_generator).theme_version_record(filename).thenReturn(None)
        when(theme_generator).generator_for_scheme(
            source, "color_scheme"
        ).thenReturn(concrete_generator)
        when(theme_generator).store_theme_version(...).thenReturn(None)
        when(theme_generator).try_apply_theme(...).thenReturn(None)

        with tempfile.TemporaryDirectory() as directory:
            when(theme_generator.sublime).packages_path().thenReturn(directory)
            generator.ensure_theme()

            self.assertEqual(concrete_generator.styles, [(
                "Marker",
                "git_savvy.marker",
                {"foreground": "#abc"},
            )])
            self.assertTrue(concrete_generator.written_path.endswith(filename))

        verify(theme_generator).store_theme_version(filename, ANY(str), True)
        verify(theme_generator).try_apply_theme(
            generator._view,
            "color_scheme",
            "Packages/User/GitSavvy/" + filename
        )


class TestResourceVersion(DeferrableTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.packages_path = os.path.join(self.temp_dir.name, "Packages")
        self.installed_packages_path = os.path.join(self.temp_dir.name, "Installed Packages")
        os.makedirs(self.packages_path)
        os.makedirs(self.installed_packages_path)

        when(theme_generator.sublime).packages_path().thenReturn(self.packages_path)
        when(theme_generator.sublime).installed_packages_path().thenReturn(
            self.installed_packages_path
        )

    def tearDown(self) -> None:
        unstub()
        self.temp_dir.cleanup()

    def test_prefers_an_unpacked_resource(self) -> None:
        resource = "Packages/Example/Scheme.sublime-color-scheme"
        path = os.path.join(self.packages_path, "Example", "Scheme.sublime-color-scheme")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as file:
            file.write("{}")

        version = theme_generator.resource_version(resource)

        self.assertEqual(version[0], "file")
        self.assertEqual(version[1], 2)

    def test_uses_package_metadata_for_an_archived_resource(self) -> None:
        resource = "Packages/Example/Scheme.sublime-color-scheme"
        path = os.path.join(self.installed_packages_path, "Example.sublime-package")
        with open(path, "wb") as file:
            file.write(b"package")

        version = theme_generator.resource_version(resource)

        self.assertEqual(version[0], "package")
        self.assertEqual(version[1], 7)


class FakeView:
    def __init__(self, settings: dict) -> None:
        self._settings = FakeViewSettings(settings)

    def settings(self) -> "FakeViewSettings":
        return self._settings


class FakeViewSettings(dict):
    def erase(self, key: str) -> None:
        self.pop(key, None)


class FakeConcreteGenerator:
    is_dirty = True

    def __init__(self) -> None:
        self.styles = []
        self.written_path = ""

    def add_scoped_style(self, name: str, scope: str, **properties: object) -> None:
        self.styles.append((name, scope, properties))

    def write_new_theme(self, path: str) -> None:
        self.written_path = path


def dependency_version(generator: ThemeGenerator, source: str) -> str:
    return generator._dependency_version(source, generator._resolved_styles())


def configured_generator(source: str, view_settings: dict | None = None) -> ThemeGenerator:
    when(theme_generator.sublime).load_settings(
        "Preferences.sublime-settings"
    ).thenReturn({"color_scheme": source})
    when(theme_generator.sublime).load_settings(
        "graph.sublime-settings"
    ).thenReturn({})
    when(theme_generator).resources_for_scheme(source).thenReturn([
        "Packages/Example/Example.sublime-color-scheme"
    ])
    when(theme_generator).resource_version(...).thenReturn(("package", "Example", 10, 20))
    when(theme_generator).color_value("test", "marker").thenReturn("#abc")
    settings = {
        "syntax": "Packages/GitSavvy/syntax/graph.sublime-syntax",
        **(view_settings or {}),
    }
    generator = ThemeGenerator.for_view(FakeView(settings))
    generator.add_scoped_style(
        "Marker",
        "git_savvy.marker",
        foreground=ColorRef("test", "marker")
    )
    return generator


def generated_theme(setting_name: str) -> str:
    return "Packages/User/GitSavvy/GitSavvy.graph.{}.hidden-color-scheme".format(
        setting_name
    )
