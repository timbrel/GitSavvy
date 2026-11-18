from __future__ import annotations
from functools import lru_cache
from itertools import chain
import os

import sublime
from sublime_plugin import ListInputHandler, TextCommand

from ..fns import filter_
from ..git_command import GitCommand
from ..runtime import enqueue_on_ui
from ..ui_mixins.input_panel import show_single_line_input_panel

from typing import List


__all__ = (
    "gs_log_graph_edit_branches",
    "gs_log_graph_edit_files",
)


class gs_log_graph_edit_branches(TextCommand):
    def run(self, edit):
        settings = self.view.settings()
        branches = settings.get("git_savvy.log_graph_view.branches", [])  # type: List[str]

        def on_done(text):
            # type: (str) -> None
            new_branches = list(filter_(text.split(' ')))
            settings.set("git_savvy.log_graph_view.branches", new_branches)
            self.view.run_command("gs_log_graph_refresh")

        show_single_line_input_panel(
            "branches", ' '.join(branches), on_done, select_text=True
        )


class gs_log_graph_edit_files(TextCommand, GitCommand):
    # GsTextCommand clones instances in run_; input() and run() must share state.
    _input_handler: GraphFilesInputHandler | None = None

    def run(self, edit: sublime.Edit, path: str | None = None, selected_index: int = -1) -> None:
        view = self.view
        if path is None:
            window = view.window()
            assert window
            window.run_command("show_overlay", {
                "overlay": "command_palette",
                "command": "gs_log_graph_edit_files",
                "args": {"selected_index": selected_index}
            })
            return

        handler, self._input_handler = self._input_handler, None
        settings = view.settings()
        apply_filters = settings.get("git_savvy.log_graph_view.apply_filters")
        paths: list[str] = settings.get("git_savvy.log_graph_view.paths", []) if apply_filters else []
        unselect = path in paths
        next_paths = [p for p in paths if p != path] if unselect else paths + [path]
        settings.set("git_savvy.log_graph_view.paths", next_paths)
        settings.set("git_savvy.log_graph_view.apply_filters", True)
        if not apply_filters:
            settings.set("git_savvy.log_graph_view.filters", "")
        view.run_command("gs_log_graph_refresh", {"assume_complete_redraw": bool(next_paths)})
        if handler and handler.keep_open:
            idx = handler.paths.index(path)
            enqueue_on_ui(view.run_command, "gs_log_graph_edit_files", {
                "selected_index": max(0, idx - 1) if unselect else next_paths.index(path)
            })

    def is_enabled(self) -> bool:
        return bool(self.view.settings().get("git_savvy.log_graph_view"))

    def input_description(self) -> str:
        return " "

    def input(self, args: dict) -> GraphFilesInputHandler | None:
        if "path" not in args:
            self._input_handler = GraphFilesInputHandler(self, args.get("selected_index", -1))
            return self._input_handler
        return None

    @lru_cache(1)
    def list_controlled_files(self, __cc: int) -> list[str]:
        output = self.git(
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            "--name-only",
            "HEAD"
        )
        return list(filter_(output.split("\0")))


class GraphFilesInputHandler(ListInputHandler):
    def __init__(self, command: gs_log_graph_edit_files, selected_index: int = -1) -> None:
        self.command = command
        self.selected_index = selected_index
        self.paths: list[str] = []
        self.selected_paths: list[str] = []
        self.keep_open = False

    def name(self) -> str:
        return "path"

    # def placeholder(self) -> str:
    #     return "Filter by path"

    def list_items(self) -> tuple[list[sublime.ListInputItem], int]:
        view = self.command.view
        settings = view.settings()
        files = self.command.list_controlled_files(view.change_count())
        self.selected_paths = (
            settings.get("git_savvy.log_graph_view.paths", [])
            if settings.get("git_savvy.log_graph_view.apply_filters")
            else []
        )
        self.paths = self.selected_paths + sorted(
            path for path in chain(files, {os.path.dirname(f) for f in files})
            if path and path not in self.selected_paths
        )
        items = [
            sublime.ListInputItem(
                (
                    f"✓ {path}" if path in self.selected_paths else
                    f"  {path}" if self.selected_paths else
                    f"{path}"
                ),
                path
            )
            for path in self.paths
        ]
        if not items:
            items.append(sublime.ListInputItem("There are no files available.", None))
        return items, self.selected_index

    def preview(self, value: str | None) -> str:
        action = "unselect" if value in self.selected_paths else "select"
        return f"[ctrl+enter] to {action}, [enter] to also close"

    def want_event(self) -> bool:
        return True

    def validate(self, value: str | None, event: dict | None = None) -> bool:
        return value in self.paths

    def confirm(self, value: str, event: dict) -> None:
        modifiers = event.get("modifier_keys", {})
        self.keep_open = modifiers.get("ctrl", False) or modifiers.get("primary", False)

    def cancel(self) -> None:
        self.command._input_handler = None
        if window := self.command.view.window():
            # Backspace can pop the input back to the general command palette.
            # Close it after Sublime has finished removing the input handler.
            enqueue_on_ui(window.run_command, "hide_overlay")
