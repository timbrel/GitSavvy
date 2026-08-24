"""
Given the resource path to a Sublime theme file, generate a new
theme and allow the consumer to augment this theme and apply it
to a view.
"""
from __future__ import annotations
from collections import OrderedDict
from functools import partial
import hashlib
import os
import threading
from xml.etree import ElementTree

import sublime
from . import util
from ..core import app_state
from ..core.fns import filter_
from ..core.runtime import enqueue_on_ui
from ..core.settings import color_value

from typing import Callable, NamedTuple, Sequence


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
_theme_lock = threading.Lock()


class ColorRef(NamedTuple):
    namespace: str
    key: str


class ScopedStyle:
    def __init__(self, name: str, scope: str, **properties: object) -> None:
        self.name = name
        self.scope = scope
        self.properties = properties


ThemeEffect = Callable[[sublime.View], None]


class ThemeGenerator():
    @classmethod
    def for_view(cls, view: sublime.View) -> ThemeGenerator:
        syntax_path = view.settings().get("syntax")
        syntax_name = (
            os.path.splitext(os.path.basename(syntax_path))[0]
            if syntax_path
            else None
        )
        return cls(view, syntax_name)

    def __init__(self, view: sublime.View, syntax_name: str | None) -> None:
        self._view = view
        self._syntax_name = syntax_name
        self._styles: tuple[ScopedStyle, ...] | None = None

    def configure(self, *styles: ScopedStyle) -> None:
        self._styles = styles
        self.ensure_theme()

    def ensure_theme(self) -> None:
        if self._styles is None:
            raise RuntimeError("theme generator is not configured")
        if not self._syntax_name:
            return

        styles = self._resolved_styles()
        scheme_configuration = self._resolved_schemes()
        effects = self._compute_effects(styles, scheme_configuration)
        apply_theme_effects(effects, [self._view])

    def _compute_effects(
        self,
        styles: Sequence[tuple[str, str, dict[str, object]]],
        scheme_configuration: Sequence[tuple[str, str]]
    ) -> list[ThemeEffect]:
        assert self._syntax_name
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
            self._ensure_scheme(self._syntax_name, color_scheme, setting_name, styles)
            for color_scheme, setting_name in scheme_configuration
        )
        return effects

    def _resolved_schemes(self) -> list[tuple[str, str]]:
        assert self._syntax_name
        preferences = sublime.load_settings("Preferences.sublime-settings")
        syntax_settings = sublime.load_settings(self._syntax_name + ".sublime-settings")

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

        with _theme_lock:
            version = self._dependency_version(color_scheme, styles)
            record = theme_version_record(filename)
            if record and record.get("version") == version:
                if not record.get("generated"):
                    return partial(maybe_erase_theme_override, setting_name)
                if os.path.isfile(full_path):
                    return partial(try_apply_theme, setting_name, theme_path)

            generator = generator_for_scheme(color_scheme, setting_name)
            for name, scope, properties in styles:
                generator.add_scoped_style(name, scope, **properties)

            generated = generator.is_dirty
            if generated:
                os.makedirs(path, exist_ok=True)
                generator.write_new_theme(full_path)

            store_theme_version(filename, version, generated)
            if generated:
                return partial(try_apply_theme, setting_name, theme_path)
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

    resource = next(
        (resource for resource in resources if resource.startswith("Packages/User/")),
        color_scheme if color_scheme in resources else resources[0]
    )
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


def apply_theme_effects(
    effects: Sequence[ThemeEffect],
    views: Sequence[sublime.View]
) -> None:
    def apply() -> None:
        for view in views:
            if view.is_valid():
                for effect in effects:
                    effect(view)

    enqueue_on_ui(apply)


def maybe_erase_theme_override(setting_name: str, view: sublime.View) -> None:
    settings = view.settings()
    theme = settings.get(setting_name)
    if isinstance(theme, str) and theme.startswith(GENERATED_THEME_PREFIX):
        settings.erase(setting_name)


def try_apply_theme(
    setting_name: str,
    theme_path: str,
    view: sublime.View,
    tries: int = 0
) -> None:
    """Safely apply new theme as color_scheme."""
    if not view.is_valid():
        return
    if view.settings().get(setting_name) == theme_path:
        return

    try:
        sublime.load_resource(theme_path)
    except Exception:
        if tries >= 8:
            print(
                'GitSavvy: The theme {} is not ready to load. Maybe restart to get colored '
                'highlights.'.format(theme_path)
            )
            return

        delay = (pow(2, tries) - 1) * 10
        sublime.set_timeout(
            lambda: try_apply_theme(setting_name, theme_path, view, tries + 1),
            delay
        )
        return

    view.settings().set(setting_name, theme_path)
