from __future__ import annotations

from functools import lru_cache
import os
import tempfile

import sublime

from GitSavvy.core.base_commands import GsTextCommand

from typing import Callable, List, Optional, TypeVar
T = TypeVar("T")

__all__ = (
    "gs_ctx_file_history",
    "gs_ctx_line_history",
    "gs_ctx_path_history",
    "gs_ctx_pick_axe",
    "gs_ctx_show_file_at_commit",
    "gs_ctx_stage_hunk",
)


CONTEXT_MENU = [
    {"caption": "-"},
    {
        "caption": "GitSavvy: Line History",
        "command": "gs_ctx_line_history",
    },
    {
        "caption": "GitSavvy: Pick-axe",
        "command": "gs_ctx_pick_axe",
    },
    {
        "caption": "GitSavvy: Stage selected hunk",
        "command": "gs_ctx_stage_hunk",
    },
    {
        "caption": "...",
        "children": [
            {
                "caption": "Repo History",
                "command": "gs_graph",
                "args": {"all": True},
            },
            {
                "caption": "   Path History",
                "command": "gs_ctx_path_history",
            },
            {
                "caption": "   File History",
                "command": "gs_ctx_file_history",
            },
            {"caption": "-"},
            {
                "caption": "Show last committed version",
                "command": "gs_ctx_show_file_at_commit",
            },
        ],
    },
]
WATCHER_KEY = "GitSavvy.context_menu"

_context_menu_settings: sublime.Settings | None = None
_disable_context_menus: bool | None = None


def start_context_menu_watcher() -> None:
    global _context_menu_settings, _disable_context_menus
    settings = sublime.load_settings("GitSavvy.sublime-settings")
    settings.clear_on_change(WATCHER_KEY)
    settings.add_on_change(WATCHER_KEY, on_context_menu_settings_changed)
    _context_menu_settings = settings
    _disable_context_menus = bool(settings.get("disable_context_menus"))
    synchronize_context_menu()


def stop_context_menu_watcher() -> None:
    global _context_menu_settings, _disable_context_menus
    if _context_menu_settings is not None:
        _context_menu_settings.clear_on_change(WATCHER_KEY)
    _context_menu_settings = None
    _disable_context_menus = None


# Provide a `CommandContext` as the global `Context` which
# is valid for this exact "runtime-task".  This is to speed-up
# the preconditions in `is_enabled` and `is_visible`.

def cached_property(fn: Callable[..., T]) -> T:
    return property(lru_cache(1)(fn))  # type: ignore[return-value]


class CommandContext:
    def __init__(self, cmd: GsTextCommand):
        self._cmd = cmd

    @cached_property
    def sel(self) -> List[sublime.Region]:
        return list(self._cmd.view.sel())

    @cached_property
    def repo_path(self) -> Optional[str]:
        return self._cmd.find_repo_path()

    @cached_property
    def file_path(self) -> Optional[str]:
        return self._cmd.file_path


Context = None


def get_context(self) -> CommandContext:
    global Context
    if not Context:
        Context = CommandContext(self)
        sublime.set_timeout(reset_context)

    return Context


def reset_context():
    global Context
    Context = None


class gs_ctx_line_history(GsTextCommand):
    def is_enabled(self) -> bool:
        ctx = get_context(self)
        return bool(
            ctx.sel
            and ctx.repo_path
        )

    def is_visible(self) -> bool:
        ctx = get_context(self)
        return bool(ctx.repo_path)

    def run(self, edit) -> None:
        self.view.run_command("gs_line_history")


class gs_ctx_stage_hunk(GsTextCommand):
    def is_enabled(self) -> bool:
        ctx = get_context(self)
        return bool(
            ctx.sel
            and ctx.repo_path
            and self.view.file_name()
            and not self.view.is_dirty()
        )

    def is_visible(self) -> bool:
        ctx = get_context(self)
        return bool(ctx.repo_path)

    def run(self, edit) -> None:
        self.view.run_command("gs_stage_hunk")


class gs_ctx_pick_axe(GsTextCommand):
    def is_enabled(self) -> bool:
        ctx = get_context(self)
        return bool(
            ctx.sel
            and ctx.repo_path
            and all(self.view.substr(r).strip() for r in ctx.sel)
        )

    def is_visible(self) -> bool:
        ctx = get_context(self)
        return bool(ctx.repo_path)

    def run(self, edit) -> None:
        self.view.run_command("gs_graph_pickaxe")


class gs_ctx_path_history(GsTextCommand):
    def is_visible(self) -> bool:
        ctx = get_context(self)
        return bool(ctx.file_path)

    def run(self, edit) -> None:
        self.window.run_command("gs_graph_current_path")


class gs_ctx_file_history(GsTextCommand):
    def is_visible(self) -> bool:
        ctx = get_context(self)
        return bool(ctx.file_path)

    def run(self, edit) -> None:
        self.window.run_command("gs_graph_current_file", {"all": False})


class gs_ctx_show_file_at_commit(GsTextCommand):
    def is_visible(self) -> bool:
        ctx = get_context(self)
        return bool(ctx.file_path)

    def run(self, edit) -> None:
        self.window.run_command("gs_show_file_at_commit")


def on_context_menu_settings_changed() -> None:
    global _disable_context_menus
    if _context_menu_settings is None:
        return

    disable_context_menus = bool(
        _context_menu_settings.get("disable_context_menus")
    )
    if disable_context_menus == _disable_context_menus:
        return

    _disable_context_menus = disable_context_menus
    synchronize_context_menu()


def synchronize_context_menu() -> None:
    path = context_menu_path()
    if _disable_context_menus:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return

    contents = sublime.encode_value(CONTEXT_MENU, pretty=True) + "\n"
    try:
        with open(path, encoding="utf-8") as file:
            if file.read() == contents:
                return
    except FileNotFoundError:
        pass

    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(dir=os.path.dirname(path))
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


def context_menu_path() -> str:
    return os.path.join(sublime.cache_path(), "GitSavvy", "Context.sublime-menu")
