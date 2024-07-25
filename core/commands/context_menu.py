from functools import lru_cache

import sublime

from GitSavvy.core.base_commands import GsTextCommand

from typing import Callable, List, Optional, TypeVar
T = TypeVar("T")

__all__ = (
    "gs_ctx_line_history",
    "gs_ctx_pick_axe",
    "gs_ctx_stage_hunk",
)


# Provide a `CommandContext` as the global `Context` which
# is valid for this exact "runtime-task".  This is to speed-up
# the preconditions in `is_enabled` and `is_visible`.

def cached_property(fn: Callable[..., T]) -> T:
    return property(lru_cache(1)(fn))  # type: ignore[return-value]


class CommandContext:
    def __init__(self, cmd: GsTextCommand):
        self._cmd = cmd

    @cached_property
    def enabled(self) -> bool:
        return not self._cmd.savvy_settings.get("disable_context_menus")

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
        return ctx.enabled and bool(ctx.repo_path)

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
        return ctx.enabled and bool(ctx.repo_path)

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
        return ctx.enabled and bool(ctx.repo_path)

    def run(self, edit) -> None:
        self.view.run_command("gs_graph_pickaxe")
