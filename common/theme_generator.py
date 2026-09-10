"""Generate GitSavvy color scheme extensions."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import os
import tempfile
import traceback

import sublime

from ..core import app_state
from ..core.runtime import enqueue_on_ui
from ..core.settings import color_value

from typing import Callable, Mapping, NamedTuple, Sequence, TypeVar
from typing_extensions import ParamSpec

P = ParamSpec("P")
T = TypeVar("T")


COLOR_SCHEME_SETTINGS = (
    "color_scheme",
    "light_color_scheme",
    "dark_color_scheme",
)
DOCUMENTATION_HEADER = (
    "// Auto-generated; do not edit manually.\n"
    "// Change the `colors` setting in GitSavvy.sublime-settings instead.\n"
)
GENERATED_SCHEME_PREFIX = "GitSavvy."
GENERATED_SCHEMES_KEY = "generated_color_scheme_extensions"
OUTPUT_EXTENSION = ".sublime-color-scheme"
WATCHER_KEY = "GitSavvy.theme_generator"

_provided_styles: dict[str, tuple[ScopedStyle, ...]] = {}
_settings_watchers: dict[str, SettingsWatcher] = {}
_watchers_started = False
_scheme_executor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="GitSavvyColorScheme"
)


def enqueue_scheme_task(
    fn: Callable[P, T],
    *args: P.args,
    **kwargs: P.kwargs
) -> None:
    future = _scheme_executor.submit(fn, *args, **kwargs)
    future.add_done_callback(report_scheme_task_error)


def report_scheme_task_error(future: Future[T]) -> None:
    error = future.exception()
    if error:
        traceback.print_exception(type(error), error, error.__traceback__)


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


def provide(syntax_name: str, styles: Sequence[ScopedStyle]) -> None:
    """Declare all color scheme rules used by a syntax."""
    declared_styles = tuple(styles)
    if _provided_styles.get(syntax_name) == declared_styles:
        return

    _provided_styles[syntax_name] = declared_styles
    if _watchers_started:
        watch_syntax_settings(syntax_name)
        schedule_refresh()


def start_watchers() -> None:
    """Prime settings watchers and defer the initial synchronization."""
    global _watchers_started
    if _watchers_started:
        return

    _watchers_started = True
    watch_settings("Preferences.sublime-settings", COLOR_SCHEME_SETTINGS)
    watch_settings("GitSavvy.sublime-settings", ("colors",))
    for syntax_name in _provided_styles:
        watch_syntax_settings(syntax_name)


def stop_watchers() -> None:
    global _watchers_started
    for watcher in list(_settings_watchers.values()):
        watcher.stop()
    _settings_watchers.clear()
    _watchers_started = False


def shutdown_executor() -> None:
    _scheme_executor.shutdown(wait=False)


def migrate_color_scheme(view: sublime.View) -> None:
    """Remove persisted references to GitSavvy's legacy generated schemes."""
    syntax_name = syntax_name_for_view(view)
    if syntax_name not in _provided_styles:
        return

    settings = view.settings()
    for setting_name in COLOR_SCHEME_SETTINGS:
        scheme = settings.get(setting_name)
        if (
            isinstance(scheme, str)
            and scheme.rsplit("/", 1)[-1].startswith(GENERATED_SCHEME_PREFIX)
        ):
            settings.erase(setting_name)


def refresh() -> None:
    provided_styles = _provided_styles.copy()
    contents = render_scheme(provided_styles)
    filenames = {
        filename
        for syntax_name in provided_styles
        for filename in effective_color_schemes(syntax_name)
    }
    synchronize_color_schemes(filenames, contents)


def synchronize_color_schemes(filenames: set[str], contents: str) -> None:
    output_dir = os.path.join(sublime.packages_path(), "User", "GitSavvy")
    version = hashlib.sha256(contents.encode("utf-8")).hexdigest()
    previous_version, previous_filenames = generated_schemes_record()
    filenames_to_write = (
        filenames - previous_filenames
        if previous_version == version
        else filenames
    )

    if filenames_to_write:
        os.makedirs(output_dir, exist_ok=True)
        for filename in filenames_to_write:
            write_file(os.path.join(output_dir, filename), contents)

    if os.path.isdir(output_dir):
        for filename in os.listdir(output_dir):
            if (
                filename.endswith(OUTPUT_EXTENSION)
                and filename not in filenames
                and not filename.startswith(GENERATED_SCHEME_PREFIX)
            ):
                os.remove(os.path.join(output_dir, filename))

    record = {
        "version": version,
        "filenames": sorted(filenames),
    }
    if record != app_state.get(GENERATED_SCHEMES_KEY):
        app_state.set(GENERATED_SCHEMES_KEY, record)


def render_scheme(
    provided_styles: Mapping[str, Sequence[ScopedStyle]] | None = None
) -> str:
    if provided_styles is None:
        provided_styles = _provided_styles.copy()

    styles: list[ScopedStyle] = []
    for syntax_name in provided_styles:
        for style in provided_styles[syntax_name]:
            if style not in styles:
                styles.append(style)

    rules = [
        {
            "name": style.name,
            "scope": style.scope,
            **{
                key: resolve_style_value(value)
                for key, value in style.properties.items()
            },
        }
        for style in styles
    ]
    scheme = {
        "variables": {},
        "globals": {},
        "rules": rules,
    }
    return DOCUMENTATION_HEADER + json.dumps(scheme, indent=4) + "\n"


def effective_color_schemes(syntax_name: str) -> list[str]:
    """Return normalized scheme filenames effective for a syntax."""
    preferences = sublime.load_settings("Preferences.sublime-settings")
    syntax_settings = sublime.load_settings(syntax_name + ".sublime-settings")

    def preference(name: str) -> object:
        return syntax_settings.get(name, preferences.get(name))

    color_scheme = preference("color_scheme")
    schemes: tuple[object, ...]
    if color_scheme == "auto":
        schemes = (
            preference("light_color_scheme"),
            preference("dark_color_scheme"),
        )
    else:
        schemes = (color_scheme,)

    return list(dict.fromkeys(
        normalized
        for scheme in schemes
        if isinstance(scheme, str)
        if (normalized := normalize_scheme_name(scheme))
    ))


def normalize_scheme_name(color_scheme: str) -> str | None:
    filename = color_scheme.rsplit("/", 1)[-1]
    lowercase = filename.lower()
    for extension in (OUTPUT_EXTENSION, ".tmtheme"):
        if lowercase.endswith(extension):
            return filename[:-len(extension)] + OUTPUT_EXTENSION
    return None


def watch_syntax_settings(syntax_name: str) -> None:
    watch_settings(syntax_name + ".sublime-settings", COLOR_SCHEME_SETTINGS)


def watch_settings(settings_name: str, keys: Sequence[str]) -> None:
    if settings_name in _settings_watchers:
        return
    _settings_watchers[settings_name] = SettingsWatcher(
        sublime.load_settings(settings_name),
        keys,
        schedule_refresh
    )


def schedule_refresh() -> None:
    # Defer once on the UI queue so all settings callbacks, including the
    # GitSavvy color cache invalidation, have completed before colors resolve.
    enqueue_on_ui(enqueue_scheme_task, refresh)


class SettingsWatcher:
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
        settings.clear_on_change(WATCHER_KEY)
        settings.add_on_change(WATCHER_KEY, self.check_for_changes)

    def stop(self) -> None:
        self.settings.clear_on_change(WATCHER_KEY)

    def check_for_changes(self) -> None:
        values = self.current_values()
        if values == self.values:
            return
        self.values = values
        self.on_change()

    def current_values(self) -> tuple[object, ...]:
        return tuple(self.settings.get(key) for key in self.keys)


def syntax_name_for_view(view: sublime.View) -> str:
    syntax_path = view.settings().get("syntax")
    if not isinstance(syntax_path, str):
        return ""
    return os.path.splitext(syntax_path.rsplit("/", 1)[-1])[0]


def resolve_style_value(value: object) -> object:
    if isinstance(value, ColorRef):
        return color_value(value.namespace, value.key)
    return value


def generated_schemes_record() -> tuple[str | None, set[str]]:
    record = app_state.get(GENERATED_SCHEMES_KEY)
    if record is None:
        return None, set()
    return record["version"], set(record["filenames"])


def write_file(path: str, contents: str) -> None:
    directory = os.path.dirname(path)
    fd, temporary_path = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(contents)
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass
        raise
