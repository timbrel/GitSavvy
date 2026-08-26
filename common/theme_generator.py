"""
Given the resource path to a Sublime theme file, generate a new
theme and allow the consumer to augment this theme and apply it
to a view.
"""
from __future__ import annotations
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
import hashlib
import os
import traceback
from xml.etree import ElementTree

import sublime
from . import util
from ..core import app_state
from ..core.fns import filter_
from ..core.runtime import assert_on_ui, enqueue_on_ui
from ..core.settings import color_value

from typing import Callable, NamedTuple, Optional, Sequence, TypeVar
from typing_extensions import ParamSpec

P = ParamSpec("P")
T = TypeVar("T")


STYLES_HEADER = """
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
"""

STYLE_TEMPLATE = """
 <dict>
    <key>name</key>
    <string>{name}</string>
    <key>scope</key>
    <string>{scope}</string>
    <key>settings</key>
    <dict>
{properties}
    </dict>
</dict>
"""

PROPERTY_TEMPLATE = """
        <key>{key}</key>
        <string>{value}</string>
"""

THEME_VERSIONS_KEY = "generated_theme_versions"
THEME_GENERATOR_VERSION = 1
GENERATED_THEME_PREFIX = "Packages/User/GitSavvy/GitSavvy."
THEME_SETTING_NAMES = (
    "color_scheme",
    "light_color_scheme",
    "dark_color_scheme",
)
_theme_executor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="GitSavvyTheme"
)
_theme_generators: dict[str, ThemeGenerator] = {}
_cached_color_schemes: dict[str, tuple[tuple[str, str], ...]] = {}
_settings_watchers: dict[str, SettingsWatcher] = {}


def register(view: sublime.View, syntax_name: str) -> ThemeGenerator:
    assert_on_ui()
    generator = _theme_generators.get(syntax_name)
    if generator is None:
        generator = _theme_generators[syntax_name] = ThemeGenerator(syntax_name)
        start_auto_update(syntax_name)
    generator._views.add(view)
    return generator


def is_registered(view: sublime.View) -> bool:
    assert_on_ui()
    return any(view in generator._views for generator in _theme_generators.values())


def unregister(view: sublime.View) -> None:
    assert_on_ui()
    for syntax_name, generator in _theme_generators.items():
        if view in generator._views:
            generator._views.remove(view)
            if not generator._views:
                _theme_generators.pop(syntax_name)
                if not _theme_generators:
                    stop_auto_update()
                else:
                    unwatch_syntax_settings(syntax_name)
            break


def start_auto_update(syntax_name: str) -> None:
    watch_settings("Preferences.sublime-settings", THEME_SETTING_NAMES)
    watch_settings("GitSavvy.sublime-settings", ("colors",))
    watch_settings(syntax_name + ".sublime-settings", THEME_SETTING_NAMES, syntax_name)


def stop_auto_update() -> None:
    for watcher in list(_settings_watchers.values()):
        watcher.stop()
    _settings_watchers.clear()


def shutdown_theme_executor() -> None:
    _theme_executor.shutdown(wait=False)


def enqueue_theme_task(
    fn: Callable[P, T],
    *args: P.args,
    **kwargs: P.kwargs
) -> None:
    future = _theme_executor.submit(fn, *args, **kwargs)
    future.add_done_callback(report_theme_task_error)


def report_theme_task_error(future: Future[T]) -> None:
    error = future.exception()
    if error:
        traceback.print_exception(type(error), error, error.__traceback__)


def watch_settings(
    settings_name: str,
    keys: Sequence[str],
    syntax_name: str | None = None
) -> None:
    if settings_name in _settings_watchers:
        return
    settings = sublime.load_settings(settings_name)
    _settings_watchers[settings_name] = SettingsWatcher(
        settings,
        keys,
        partial(schedule_refresh, syntax_name)
    )


def unwatch_syntax_settings(syntax_name: str) -> None:
    settings_name = syntax_name + ".sublime-settings"
    watcher = _settings_watchers.pop(settings_name, None)
    if watcher:
        watcher.stop()


class SettingsWatcher:
    callback_key = "GitSavvy.theme_generator"

    def __init__(
        self,
        settings: sublime.Settings,
        keys: Sequence[str],
        on_change: Callable[[], None]
    ) -> None:
        self.settings = settings
        self.keys = keys
        self.on_change = on_change
        self.values = self.current_values()
        settings.clear_on_change(self.callback_key)
        settings.add_on_change(self.callback_key, self.check_for_changes)

    def stop(self) -> None:
        self.settings.clear_on_change(self.callback_key)

    def check_for_changes(self) -> None:
        values = self.current_values()
        if values == self.values:
            return
        self.values = values
        self.on_change()

    def current_values(self) -> tuple[str, ...]:
        return tuple(self.settings.get(key) for key in self.keys)


def schedule_refresh(syntax_name: str | None) -> None:
    assert_on_ui()
    generators = [
        generator
        for name, generator in _theme_generators.items()
        if syntax_name is None or name == syntax_name
        if generator._styles is not None
    ]

    if generators:
        # Let all settings callbacks, including the color cache invalidation,
        # finish before resolving the new values.
        enqueue_on_ui(enqueue_theme_task, refresh_generators, generators)


def refresh_generators(generators: Sequence[ThemeGenerator]) -> None:
    for generator in generators:
        if generator._views:
            generator.refresh_all()


class ColorRef(NamedTuple):
    namespace: str
    key: str


class ScopedStyle:
    def __init__(self, name: str, scope: str, **properties: object) -> None:
        self.name = name
        self.scope = scope
        self.properties = properties

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ScopedStyle):
            return NotImplemented
        return (
            self.name == other.name
            and self.scope == other.scope
            and self.properties == other.properties
        )


ThemeSetter = Callable[[], None]
ThemeWaiter = Callable[[Callable[[], None]], None]


class PreparedThemeEffect(NamedTuple):
    setter: ThemeSetter | None
    waiter: ThemeWaiter | None = None
    color_scheme: tuple[str, str] | None = None


ThemeEffect = Callable[[sublime.Settings], Optional[PreparedThemeEffect]]


class ThemeConfigurator:
    def __init__(self, generator: ThemeGenerator, view: sublime.View) -> None:
        self._generator = generator
        self._view = view

    def configure(self, *styles: ScopedStyle) -> None:
        apply_color_schemes_if_cached(self._view, self._generator.syntax_name)
        enqueue_theme_task(self._generator.configure_view, self._view, styles)


class ThemeGenerator():
    @classmethod
    def for_view(cls, view: sublime.View) -> ThemeConfigurator:
        assert_on_ui()
        syntax_path = view.settings().get("syntax")
        syntax_name = os.path.splitext(os.path.basename(syntax_path))[0]
        generator = register(view, syntax_name)
        return ThemeConfigurator(generator, view)

    def __init__(self, syntax_name: str) -> None:
        self.syntax_name = syntax_name
        self._styles: tuple[ScopedStyle, ...] | None = None
        self._effects: tuple[ThemeEffect, ...] | None = None
        self._views: set[sublime.View] = set()

    def configure_view(self, view: sublime.View, styles: tuple[ScopedStyle, ...]) -> None:
        if self._styles != styles:
            self._styles = styles
            self.refresh_all()
        else:
            assert self._effects
            apply_theme_effects(self.syntax_name, self._effects, [view])

    def refresh_all(self) -> None:
        if self._styles is None:
            raise RuntimeError("theme generator is not configured")

        styles = self._resolved_styles()
        scheme_configuration = self._resolved_schemes()
        self._effects = effects = self._compute_effects(styles, scheme_configuration)

        apply_theme_effects(self.syntax_name, effects, list(self._views))

    def _compute_effects(
        self,
        styles: Sequence[tuple[str, str, dict[str, object]]],
        scheme_configuration: Sequence[tuple[str, str]]
    ) -> tuple[ThemeEffect, ...]:
        active_setting_names = {
            setting_name
            for _, setting_name in scheme_configuration
        }
        effects: list[ThemeEffect] = [
            partial(maybe_erase_theme_override, setting_name)
            for setting_name in THEME_SETTING_NAMES
            if setting_name not in active_setting_names
        ]
        effects.extend(
            self._ensure_scheme(self.syntax_name, color_scheme, setting_name, styles)
            for color_scheme, setting_name in scheme_configuration
        )
        return tuple(effects)

    def _resolved_schemes(self) -> list[tuple[str, str]]:
        preferences = sublime.load_settings("Preferences.sublime-settings")
        syntax_settings = sublime.load_settings(self.syntax_name + ".sublime-settings")

        def preference(name: str):
            return syntax_settings.get(name, preferences.get(name))

        color_scheme = preference("color_scheme")
        if not color_scheme:
            return []

        if color_scheme == "auto":
            def scheme_for_key(name: str) -> tuple[str, str] | None:
                color_scheme = preference(name)
                return (color_scheme, name) if color_scheme else None

            return list(filter_((
                scheme_for_key("light_color_scheme"),
                scheme_for_key("dark_color_scheme"),
            )))

        return [(color_scheme, "color_scheme")]

    def _resolved_styles(self) -> list[tuple[str, str, dict[str, object]]]:
        assert self._styles is not None
        return [
            (
                style.name,
                style.scope,
                {
                    key: resolve_style_value(value)
                    for key, value in style.properties.items()
                }
            )
            for style in self._styles
        ]

    def _ensure_scheme(
        self,
        syntax_name: str,
        color_scheme: str,
        setting_name: str,
        styles: Sequence[tuple[str, str, dict[str, object]]]
    ) -> ThemeEffect:
        extension = hidden_extension_for_scheme(color_scheme)
        filename = "GitSavvy.{}.{}.{}".format(syntax_name, setting_name, extension)
        path = os.path.join(sublime.packages_path(), "User", "GitSavvy")
        full_path = os.path.join(path, filename)
        theme_path = "/".join(("Packages", "User", "GitSavvy", filename))

        new_resource = not os.path.isfile(full_path)
        version = self._dependency_version(color_scheme, styles)
        record = theme_version_record(filename)
        if record and record.get("version") == version:
            if not record.get("generated"):
                return partial(maybe_erase_theme_override, setting_name)
            if not new_resource:
                return partial(set_theme, setting_name, theme_path, None)

        generator = generator_for_scheme(color_scheme, setting_name)
        for name, scope, properties in styles:
            generator.add_scoped_style(name, scope, **properties)

        generated = generator.is_dirty
        if generated:
            os.makedirs(path, exist_ok=True)
            generator.write_new_theme(full_path)

        store_theme_version(filename, version, generated)
        if generated:
            waiter = (
                partial(wait_for_resource, theme_path)
                if new_resource
                # Give Sublime Text a chance to parse an updated resource.
                # Otherwise, changing the setting causes one draw with the
                # old theme and another with the updated theme.
                else partial(wait, 100)
            )
            return partial(set_theme, setting_name, theme_path, waiter)
        return partial(maybe_erase_theme_override, setting_name)

    def _dependency_version(
        self,
        color_scheme: str,
        styles: Sequence[tuple[str, str, dict[str, object]]]
    ) -> str:
        digest = hashlib.sha256(str(THEME_GENERATOR_VERSION).encode())
        for resource in resources_for_scheme(color_scheme):
            digest.update(resource.encode())
            digest.update(repr(resource_version(resource)).encode())
        digest.update(repr(styles).encode())
        return digest.hexdigest()


class AbstractThemeGenerator:
    """
    Given the path to a theme file, parse it, allow transformations
    on the data, save it, and apply the transformed theme to a view.
    """

    hidden_theme_extension = None  # type: str

    def __init__(self, original_color_scheme: str, setting_name: str) -> None:
        self.setting_name = setting_name
        self._dirty = False
        self.color_scheme_string = load_scheme_resource(original_color_scheme)

    def add_scoped_style(self, name, scope, **kwargs):
        # type: (str, str, object) -> None
        """
        Add scope-specific styles to the theme.  A unique name should be provided
        as well as a scope corresponding to regions of text.  Any keyword arguments
        will be used as key and value for the newly-defined style.
        """
        if scope in self.color_scheme_string:
            return

        self._dirty = True
        self._add_scoped_style(name, scope, **kwargs)

    def _add_scoped_style(self, name, scope, **kwargs):
        raise NotImplementedError

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def write_new_theme(self, path: str) -> None:
        """Write the new theme on disk."""
        raise NotImplementedError


class XMLThemeGenerator(AbstractThemeGenerator):
    """
    A theme generator for the vintage syntax `.tmTheme`
    """

    hidden_theme_extension = "hidden-tmTheme"

    def __init__(self, original_color_scheme, setting_name="color_scheme"):
        # type: (str, str) -> None
        super().__init__(original_color_scheme, setting_name)
        self.plist = ElementTree.XML(self.color_scheme_string)
        styles = self.plist.find("./dict/array")
        assert styles
        self.styles = styles

    def _add_scoped_style(self, name, scope, **kwargs):
        properties = "".join(PROPERTY_TEMPLATE.format(key=k, value=v) for k, v in kwargs.items())
        new_style = STYLE_TEMPLATE.format(name=name, scope=scope, properties=properties)
        self.styles.append(ElementTree.XML(new_style))

    def write_new_theme(self, path: str) -> None:
        with util.file.safe_open(path, "wb", buffering=0) as out_f:
            out_f.write(STYLES_HEADER.encode("utf-8"))
            out_f.write(ElementTree.tostring(self.plist, encoding="utf-8"))


class JSONThemeGenerator(AbstractThemeGenerator):
    """
    A theme generator for the new syntax `.sublime-color-scheme`
    """

    hidden_theme_extension = "hidden-color-scheme"

    def __init__(self, original_color_scheme, setting_name="color_scheme"):
        # type: (str, str) -> None
        super().__init__(original_color_scheme, setting_name)
        self.dict = OrderedDict(sublime.decode_value(self.color_scheme_string))

    def _add_scoped_style(self, name, scope, **kwargs):
        new_rule = OrderedDict([("name", name), ("scope", scope)])
        for (k, v) in kwargs.items():
            new_rule[k] = v
        self.dict["rules"].insert(0, new_rule)

    def write_new_theme(self, path: str) -> None:
        with util.file.safe_open(path, "wb", buffering=0) as out_f:
            out_f.write(sublime.encode_value(self.dict, pretty=True).encode("utf-8"))


def resolve_style_value(value: object) -> object:
    if isinstance(value, ColorRef):
        return color_value(value.namespace, value.key)
    return value


def generator_for_scheme(color_scheme: str, setting_name: str) -> AbstractThemeGenerator:
    if color_scheme.endswith(".tmTheme"):
        return XMLThemeGenerator(color_scheme, setting_name)
    return JSONThemeGenerator(color_scheme, setting_name)


def hidden_extension_for_scheme(color_scheme: str) -> str:
    if color_scheme.endswith(".tmTheme"):
        return XMLThemeGenerator.hidden_theme_extension
    return JSONThemeGenerator.hidden_theme_extension


def theme_version_record(filename: str) -> dict | None:
    versions = app_state.get(THEME_VERSIONS_KEY, {})
    if not isinstance(versions, dict):
        return None
    record = versions.get(filename)
    return record if isinstance(record, dict) else None


def store_theme_version(filename: str, version: str, generated: bool) -> None:
    versions = app_state.get(THEME_VERSIONS_KEY, {})
    versions = dict(versions) if isinstance(versions, dict) else {}
    versions[filename] = {
        "version": version,
        "generated": generated,
    }
    app_state.set(THEME_VERSIONS_KEY, versions)


def load_scheme_resource(color_scheme: str) -> str:
    resources = resources_for_scheme(color_scheme)
    if not resources:
        raise IOError("{} cannot be found".format(color_scheme))

    # Prefer package resources over cached copies.  Within packages, deeper
    # resources are typically partial customizations, so start from the complete
    # base that the generated scheme can safely copy.
    resource = min(resources, key=lambda resource: (
        not resource.startswith("Packages/"),
        resource.count("/")
    ))
    return sublime.load_resource(resource)


def resources_for_scheme(color_scheme: str) -> list[str]:
    filename = color_scheme.rsplit("/", 1)[-1]
    resources = list(sublime.find_resources(filename))
    if color_scheme.startswith(("Packages/", "Cache/")) and color_scheme not in resources:
        resources.insert(0, color_scheme)
    return resources


def resource_version(resource: str) -> tuple:
    if resource.startswith("Packages/"):
        parts = resource.split("/")
        if len(parts) < 3:
            return ("missing", resource)

        loose_path = os.path.join(sublime.packages_path(), *parts[1:])
        if os.path.isfile(loose_path):
            return file_version("file", loose_path)

        archive_path = os.path.join(
            sublime.installed_packages_path(),
            parts[1] + ".sublime-package"
        )
        if os.path.isfile(archive_path):
            return file_version("package", archive_path)

        return ("builtin", sublime.version())

    if resource.startswith("Cache/"):
        cache_path = os.path.join(sublime.cache_path(), *resource.split("/")[1:])
        if os.path.isfile(cache_path):
            return file_version("cache", cache_path)
        return ("missing", resource)

    if os.path.isfile(resource):
        return file_version("file", resource)
    return ("missing", resource)


def file_version(kind: str, path: str) -> tuple:
    stat = os.stat(path)
    return (kind, stat.st_size, stat.st_mtime_ns)


def apply_color_schemes_if_cached(view: sublime.View, syntax_name: str) -> None:
    assert_on_ui()
    settings = view.settings()
    for setting_name, theme_path in _cached_color_schemes.get(syntax_name, ()):
        if settings.get(setting_name) != theme_path:
            settings.set(setting_name, theme_path)


def apply_theme_effects(
    syntax_name: str,
    effects: Sequence[ThemeEffect],
    views: Sequence[sublime.View]
) -> None:
    def prepare_and_apply() -> None:
        prepared_effects = [
            prepared_effect
            for view in views
            if view.is_valid()
            if (settings := view.settings())
            for effect in effects
            if (prepared_effect := effect(settings))
        ]

        apply_after_waiters(prepared_effects)

    def apply(prepared_effects: Sequence[PreparedThemeEffect]) -> None:
        _cached_color_schemes[syntax_name] = tuple(dict.fromkeys(
            effect.color_scheme
            for effect in prepared_effects
            if effect.color_scheme
        ))
        apply_setters(prepared_effects)

    def apply_after_waiters(
        prepared_effects: Sequence[PreparedThemeEffect]
    ) -> None:
        waiters = tuple(dict.fromkeys(
            effect.waiter
            for effect in prepared_effects
            if effect.waiter
        ))
        if not waiters:
            apply(prepared_effects)
            return

        remaining = len(waiters)

        def waiter_finished() -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                apply(prepared_effects)

        for waiter in waiters:
            waiter(waiter_finished)

    enqueue_on_ui(prepare_and_apply)


def apply_setters(effects: Sequence[PreparedThemeEffect]) -> None:
    for effect in effects:
        if effect.setter:
            effect.setter()


def wait(delay: int, on_ready: Callable[[], None]) -> None:
    sublime.set_timeout(on_ready, delay)


def wait_for_resource(
    resource_path: str,
    on_ready: Callable[[], None],
    tries: int = 0
) -> None:
    try:
        sublime.load_resource(resource_path)
    except Exception:
        if tries >= 8:
            print(
                'GitSavvy: The theme {} is not ready to load. Maybe restart to get colored '
                'highlights.'.format(resource_path)
            )
            return

        delay = (pow(2, tries) - 1) * 10
        sublime.set_timeout(
            lambda: wait_for_resource(resource_path, on_ready, tries + 1),
            delay
        )
        return

    on_ready()


def maybe_erase_theme_override(
    setting_name: str,
    settings: sublime.Settings
) -> PreparedThemeEffect | None:
    theme = settings.get(setting_name)
    if isinstance(theme, str) and theme.startswith(GENERATED_THEME_PREFIX):
        return PreparedThemeEffect(partial(settings.erase, setting_name))
    return None


def set_theme(
    setting_name: str,
    theme_path: str,
    waiter: ThemeWaiter | None,
    settings: sublime.Settings
) -> PreparedThemeEffect:
    color_scheme = (setting_name, theme_path)
    if settings.get(setting_name) != theme_path:
        return PreparedThemeEffect(
            partial(settings.set, setting_name, theme_path),
            waiter,
            color_scheme
        )
    return PreparedThemeEffect(None, color_scheme=color_scheme)
