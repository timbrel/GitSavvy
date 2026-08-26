from __future__ import annotations
from functools import partial
import os
import tempfile

from unittesting import DeferrableTestCase

from GitSavvy.common import theme_generator
from GitSavvy.common.theme_generator import ColorRef, ScopedStyle, ThemeGenerator
from GitSavvy.tests.mockito import ANY, unstub, verify, when


class TestThemeGenerator(DeferrableTestCase):
    def setUp(self) -> None:
        when(theme_generator).enqueue_on_ui(...).thenAnswer(
            lambda callback, *args: callback(*args)
        )
        when(theme_generator).enqueue_theme_task(...).thenAnswer(
            lambda callback, *args: callback(*args)
        )

    def tearDown(self) -> None:
        theme_generator.stop_auto_update()
        theme_generator._theme_generators.clear()
        theme_generator._cached_color_schemes.clear()
        unstub()

    def test_configuration_runs_on_the_theme_executor(self) -> None:
        view = FakeView({})
        generator = ThemeGenerator("plain")
        configurator = theme_generator.ThemeConfigurator(generator, view)
        when(theme_generator).enqueue_theme_task(
            generator.configure_view, view, ()
        ).thenReturn(None)

        configurator.configure()

        verify(theme_generator).enqueue_theme_task(
            generator.configure_view,
            view,
            ()
        )

    def test_configure_applies_cached_color_schemes_before_enqueuing(self) -> None:
        color_scheme = generated_theme("color_scheme")
        dark_color_scheme = generated_theme("dark_color_scheme")
        view = FakeView({"color_scheme": "Packages/Example/Example.sublime-color-scheme"})
        generator = ThemeGenerator("graph")
        configurator = theme_generator.ThemeConfigurator(generator, view)
        theme_generator._cached_color_schemes["graph"] = (
            ("color_scheme", color_scheme),
            ("dark_color_scheme", dark_color_scheme),
        )
        when(theme_generator).enqueue_theme_task(
            generator.configure_view, view, ()
        ).thenAnswer(
            lambda callback, *args: self.assertEqual(
                view.settings()["color_scheme"], color_scheme
            )
        )

        configurator.configure()

        self.assertEqual(view.settings()["color_scheme"], color_scheme)
        self.assertEqual(view.settings()["dark_color_scheme"], dark_color_scheme)

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

        generator = ThemeGenerator("graph")

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

        generator = ThemeGenerator("graph")

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

        generator = ThemeGenerator("graph")

        self.assertEqual(generator._resolved_schemes(), [
            (light_scheme, "light_color_scheme"),
            (dark_scheme, "dark_color_scheme"),
        ])

    def test_relevant_setting_changes_refresh_registered_views(self) -> None:
        source = "Packages/Example/Example.sublime-color-scheme"
        preferences = WatchableSettings({"color_scheme": source})
        syntax_settings = WatchableSettings({})
        savvy_settings = WatchableSettings({"colors": {}})
        when(theme_generator.sublime).load_settings(
            "Preferences.sublime-settings"
        ).thenReturn(preferences)
        when(theme_generator.sublime).load_settings(
            "GitSavvy.sublime-settings"
        ).thenReturn(savvy_settings)
        when(theme_generator.sublime).load_settings(
            "graph.sublime-settings"
        ).thenReturn(syntax_settings)
        when(theme_generator.sublime).set_timeout_async(...).thenAnswer(
            lambda callback: callback()
        )
        when(theme_generator).resources_for_scheme(source).thenReturn([
            "Packages/Example/Example.sublime-color-scheme"
        ])
        when(theme_generator).resource_version(...).thenReturn(
            ("package", "Example", 10, 20)
        )
        when(theme_generator).color_value("test", "marker").thenReturn("#abc")

        view = FakeView({
            "syntax": "Packages/GitSavvy/syntax/graph.sublime-syntax",
            "color_scheme": generated_theme("color_scheme"),
        })
        configurator = ThemeGenerator.for_view(view)
        version = dependency_version(configurator, source)
        filename = "GitSavvy.graph.color_scheme.hidden-color-scheme"
        when(theme_generator).theme_version_record(filename).thenReturn({
            "version": version,
            "generated": False,
        })
        configurator.configure(marker_style())
        verify(theme_generator, times=1).theme_version_record(filename)

        view.settings()["color_scheme"] = generated_theme("color_scheme")
        savvy_settings.set("colors", {"test": {"marker": "#def"}})

        self.assertNotIn("color_scheme", view.settings())
        verify(theme_generator, times=2).theme_version_record(filename)

        view.settings()["color_scheme"] = generated_theme("color_scheme")
        syntax_settings.set("color_scheme", source)

        self.assertNotIn("color_scheme", view.settings())
        verify(theme_generator, times=3).theme_version_record(filename)

        view.settings()["color_scheme"] = generated_theme("color_scheme")
        preferences.set("light_color_scheme", "Packages/Example/Light.sublime-color-scheme")

        self.assertNotIn("color_scheme", view.settings())
        verify(theme_generator, times=4).theme_version_record(filename)

    def test_switching_to_auto_removes_old_generated_settings(self) -> None:
        when(theme_generator.sublime).load_settings(
            "Preferences.sublime-settings"
        ).thenReturn(WatchableSettings({"color_scheme": "auto"}))
        when(theme_generator.sublime).load_settings(
            "graph.sublime-settings"
        ).thenReturn(WatchableSettings({}))
        when(theme_generator.sublime).load_settings(
            "GitSavvy.sublime-settings"
        ).thenReturn(WatchableSettings({"colors": {}}))
        view = FakeView({
            "syntax": "Packages/GitSavvy/syntax/graph.sublime-syntax",
            "color_scheme": generated_theme("color_scheme"),
            "light_color_scheme": generated_theme("light_color_scheme"),
            "dark_color_scheme": generated_theme("dark_color_scheme"),
        })

        ThemeGenerator.for_view(view).configure()

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

        generator.configure(marker_style())

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

        generator.configure(marker_style())

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
        with tempfile.TemporaryDirectory() as directory:
            when(theme_generator.sublime).packages_path().thenReturn(directory)
            path = os.path.join(directory, "User", "GitSavvy", filename)
            os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8") as file:
                file.write("{}")

            generator.configure(marker_style())
            generator._generator.refresh_all()

        self.assertEqual(generator._view.settings()["color_scheme"], generated)

    def test_shared_generator_attaches_new_views_without_recomputing(self) -> None:
        source = "Packages/Example/Example.sublime-color-scheme"
        first = configured_generator(source, {
            "color_scheme": generated_theme("color_scheme"),
        })
        second = ThemeGenerator.for_view(FakeView({
            "syntax": "Packages/GitSavvy/syntax/graph.sublime-syntax",
            "color_scheme": generated_theme("color_scheme"),
        }))
        version = dependency_version(first, source)
        filename = "GitSavvy.graph.color_scheme.hidden-color-scheme"
        when(theme_generator).theme_version_record(filename).thenReturn({
            "version": version,
            "generated": False,
        })

        first.configure(marker_style())
        second.configure(marker_style())

        self.assertIs(first._generator, second._generator)
        self.assertNotIn("color_scheme", first._view.settings())
        self.assertNotIn("color_scheme", second._view.settings())
        verify(theme_generator, times=1).theme_version_record(filename)

        first._view.settings()["color_scheme"] = generated_theme("color_scheme")
        second._view.settings()["color_scheme"] = generated_theme("color_scheme")
        first._generator.refresh_all()

        self.assertNotIn("color_scheme", first._view.settings())
        self.assertNotIn("color_scheme", second._view.settings())
        verify(theme_generator, times=2).theme_version_record(filename)

        theme_generator.unregister(first._view)
        self.assertIn("graph.sublime-settings", theme_generator._settings_watchers)
        theme_generator.unregister(second._view)
        self.assertNotIn("graph.sublime-settings", theme_generator._settings_watchers)

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
        when(theme_generator.sublime).load_resource(...).thenReturn("{}")

        with tempfile.TemporaryDirectory() as directory:
            when(theme_generator.sublime).packages_path().thenReturn(directory)
            generator.configure(marker_style())

            self.assertEqual(concrete_generator.styles, [(
                "Marker",
                "git_savvy.marker",
                {"foreground": "#abc"},
            )])
            self.assertTrue(concrete_generator.written_path.endswith(filename))

        verify(theme_generator).store_theme_version(filename, ANY(str), True)
        theme_path = "Packages/User/GitSavvy/" + filename
        self.assertEqual(generator._view.settings()["color_scheme"], theme_path)
        self.assertEqual(
            theme_generator._cached_color_schemes["graph"],
            (("color_scheme", theme_path),)
        )

    def test_rewriting_an_existing_scheme_waits_before_changing_the_setting(self) -> None:
        source = "Packages/Example/Example.sublime-color-scheme"
        generator = configured_generator(source)
        concrete_generator = FakeConcreteGenerator()
        filename = "GitSavvy.graph.color_scheme.hidden-color-scheme"

        when(theme_generator).theme_version_record(filename).thenReturn(None)
        when(theme_generator).generator_for_scheme(
            source, "color_scheme"
        ).thenReturn(concrete_generator)
        when(theme_generator).store_theme_version(...).thenReturn(None)
        scheduled = []
        when(theme_generator.sublime).set_timeout(...).thenAnswer(
            lambda callback, delay: scheduled.append((callback, delay))
        )

        with tempfile.TemporaryDirectory() as directory:
            when(theme_generator.sublime).packages_path().thenReturn(directory)
            path = os.path.join(directory, "User", "GitSavvy", filename)
            os.makedirs(os.path.dirname(path))
            with open(path, "w", encoding="utf-8") as file:
                file.write("{}")

            generator.configure(marker_style())

        verify(theme_generator.sublime, times=0).load_resource(...)
        self.assertNotIn("color_scheme", generator._view.settings())
        self.assertEqual(len(scheduled), 1)
        callback, delay = scheduled.pop()
        self.assertEqual(delay, 100)

        callback()

        self.assertEqual(
            generator._view.settings()["color_scheme"],
            "Packages/User/GitSavvy/" + filename
        )

    def test_theme_waiters_do_not_wait_if_the_settings_will_not_change(self) -> None:
        theme_path = generated_theme("color_scheme")
        new_resource = generated_theme("dark_color_scheme")
        view = FakeView({
            "color_scheme": theme_path,
            "dark_color_scheme": new_resource,
        })
        when(theme_generator.sublime).set_timeout(...).thenReturn(None)
        when(theme_generator.sublime).load_resource(...).thenReturn("{}")
        effects = (
            partial(
                theme_generator.set_theme,
                "color_scheme",
                theme_path,
                partial(theme_generator.wait, 100)
            ),
            partial(
                theme_generator.set_theme,
                "dark_color_scheme",
                new_resource,
                partial(theme_generator.wait_for_resource, new_resource)
            ),
        )

        theme_generator.apply_theme_effects("graph", effects, [view])

        verify(theme_generator.sublime, times=0).set_timeout(...)
        verify(theme_generator.sublime, times=0).load_resource(...)
        self.assertEqual(view.settings()["color_scheme"], theme_path)
        self.assertEqual(view.settings()["dark_color_scheme"], new_resource)

    def test_waits_for_new_resources_before_applying_all_view_settings(self) -> None:
        resource = "Packages/User/GitSavvy/GitSavvy.graph.color_scheme.hidden-color-scheme"
        second_resource = (
            "Packages/User/GitSavvy/GitSavvy.graph.dark_color_scheme.hidden-color-scheme"
        )
        view = FakeView({
            "color_scheme": "Packages/Example/Example.sublime-color-scheme",
            "light_color_scheme": generated_theme("light_color_scheme"),
        })
        settings = view.settings()
        callbacks = []
        when(theme_generator.sublime).load_resource(resource).thenRaise(
            IOError("not indexed yet")
        ).thenReturn("{}")
        when(theme_generator.sublime).load_resource(second_resource).thenReturn("{}")
        when(theme_generator.sublime).set_timeout(...).thenAnswer(
            lambda callback, delay: callbacks.append(callback)
        )
        effects = (
            partial(theme_generator.maybe_erase_theme_override, "light_color_scheme"),
            partial(
                theme_generator.set_theme,
                "color_scheme",
                resource,
                partial(theme_generator.wait_for_resource, resource)
            ),
            partial(
                theme_generator.set_theme,
                "dark_color_scheme",
                second_resource,
                partial(theme_generator.wait_for_resource, second_resource)
            ),
        )

        theme_generator.apply_theme_effects("graph", effects, [view])

        self.assertEqual(len(callbacks), 1)
        verify(theme_generator.sublime).load_resource(second_resource)
        self.assertEqual(
            settings["light_color_scheme"],
            generated_theme("light_color_scheme")
        )
        self.assertEqual(
            settings["color_scheme"],
            "Packages/Example/Example.sublime-color-scheme"
        )

        retry_first_resource = callbacks.pop()
        retry_first_resource()

        self.assertNotIn("light_color_scheme", settings)
        self.assertEqual(settings["color_scheme"], resource)
        self.assertEqual(settings["dark_color_scheme"], second_resource)


class TestLoadSchemeResource(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    def test_loads_the_shallowest_resource(self) -> None:
        source = "Packages/User/Color Schemes/Example.sublime-color-scheme"
        pristine = "Packages/Example/Example.sublime-color-scheme"
        when(theme_generator).resources_for_scheme(source).thenReturn([
            source,
            pristine,
        ])
        when(theme_generator.sublime).load_resource(pristine).thenReturn("pristine")

        contents = theme_generator.load_scheme_resource(source)

        self.assertEqual(contents, "pristine")
        verify(theme_generator.sublime).load_resource(pristine)

    def test_prefers_package_resources_over_cache_resources(self) -> None:
        source = "Cache/Example.sublime-color-scheme"
        pristine = "Packages/Example/Color Schemes/Example.sublime-color-scheme"
        when(theme_generator).resources_for_scheme(source).thenReturn([
            source,
            pristine,
        ])
        when(theme_generator.sublime).load_resource(pristine).thenReturn("pristine")

        contents = theme_generator.load_scheme_resource(source)

        self.assertEqual(contents, "pristine")
        verify(theme_generator.sublime).load_resource(pristine)


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

    def id(self) -> int:
        return id(self)

    def is_valid(self) -> bool:
        return True

    def settings(self) -> "FakeViewSettings":
        return self._settings


class FakeViewSettings(dict):
    def erase(self, key: str) -> None:
        self.pop(key, None)

    def set(self, key: str, value: object) -> None:
        self[key] = value


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


class FakeConcreteGenerator:
    is_dirty = True

    def __init__(self) -> None:
        self.styles = []
        self.written_path = ""

    def add_scoped_style(self, name: str, scope: str, **properties: object) -> None:
        self.styles.append((name, scope, properties))

    def write_new_theme(self, path: str) -> None:
        self.written_path = path


def dependency_version(generator, source: str) -> str:
    return generator._generator._dependency_version(source, [(
        "Marker",
        "git_savvy.marker",
        {"foreground": "#abc"},
    )])


def marker_style() -> ScopedStyle:
    return ScopedStyle(
        "Marker",
        "git_savvy.marker",
        foreground=ColorRef("test", "marker")
    )


def configured_generator(source: str, view_settings: dict | None = None) -> ThemeGenerator:
    when(theme_generator.sublime).load_settings(
        "Preferences.sublime-settings"
    ).thenReturn(WatchableSettings({"color_scheme": source}))
    when(theme_generator.sublime).load_settings(
        "graph.sublime-settings"
    ).thenReturn(WatchableSettings({}))
    when(theme_generator.sublime).load_settings(
        "GitSavvy.sublime-settings"
    ).thenReturn(WatchableSettings({"colors": {}}))
    when(theme_generator).resources_for_scheme(source).thenReturn([
        "Packages/Example/Example.sublime-color-scheme"
    ])
    when(theme_generator).resource_version(...).thenReturn(("package", "Example", 10, 20))
    when(theme_generator).color_value("test", "marker").thenReturn("#abc")
    settings = {
        "syntax": "Packages/GitSavvy/syntax/graph.sublime-syntax",
        **(view_settings or {}),
    }
    return ThemeGenerator.for_view(FakeView(settings))


def generated_theme(setting_name: str) -> str:
    return "Packages/User/GitSavvy/GitSavvy.graph.{}.hidden-color-scheme".format(
        setting_name
    )
