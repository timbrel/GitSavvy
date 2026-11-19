from __future__ import annotations
from functools import lru_cache
from itertools import chain
import os

import sublime
from sublime_plugin import ListInputHandler, TextCommand

from ..commands.checkout import branch_ref
from ..fns import filter_
from ..git_command import GitCommand
from ..git_mixins.branches import Branch
from ..runtime import enqueue_on_ui


__all__ = (
    "gs_log_graph_edit_branches",
    "gs_log_graph_edit_files",
)


class gs_log_graph_edit_branches(TextCommand, GitCommand):
    # Keep input() and run() on the same instance, as for the file picker.
    _input_handler: GraphBranchesInputHandler | None = None

    def run(
        self, edit: sublime.Edit, branch: str | None = None,
        selected_index: int = 0, action: str = "toggle_choice", keep_all_mode: bool = False
    ) -> None:
        view = self.view
        if branch is None:
            window = view.window()
            assert window
            window.run_command("show_overlay", {
                "overlay": "command_palette",
                "command": "gs_log_graph_edit_branches",
                "args": {"selected_index": selected_index}
            })
            return

        handler, self._input_handler = self._input_handler, None
        if handler is None:
            handler = GraphBranchesInputHandler(self)
            handler.list_items()
        branch = handler.resolve_branch(branch)
        settings = view.settings()
        choices = handler.choices
        subset = handler.subset
        if action == "toggle_visibility":
            # Clicks only toggle visibility. The last subset survives all mode.
            active = [] if settings.get("git_savvy.log_graph_view.all_branches") else subset
            next_active = toggle_ref(active, branch)
            subset = next_active or active
        else:
            # The picker edits the controls themselves, including dormant ones.
            if branch in choices:
                choices = [ref for ref in choices if ref != branch]
                subset = [ref for ref in subset if ref != branch]
            elif action == "remove":
                return  # The label may still be on screen while the graph redraws.
            else:
                choices = choices + [branch]
                subset = subset + [branch]
            next_active = [] if keep_all_mode and settings.get("git_savvy.log_graph_view.all_branches") else subset

        settings.set("git_savvy.log_graph_view.branch_choices", choices)
        settings.set("git_savvy.log_graph_view.branches", subset)
        settings.set("git_savvy.log_graph_view.branch_upstreams", handler.upstreams_for(subset))
        settings.set("git_savvy.log_graph_view.all_branches", not next_active)
        settings.set("git_savvy.log_graph_view.overview", False)
        view.run_command("gs_log_graph_refresh", {"assume_complete_redraw": True})
        if handler.keep_open:
            next_refs = handler.ordered_refs(choices)
            enqueue_on_ui(view.run_command, "gs_log_graph_edit_branches", {
                "selected_index": next_refs.index(branch) if branch in next_refs else 0
            })

    def is_enabled(self) -> bool:
        return bool(self.view.settings().get("git_savvy.log_graph_view"))

    def input_description(self) -> str:
        return " "

    def input(self, args: dict) -> GraphBranchesInputHandler | None:
        if "branch" not in args:
            self._input_handler = GraphBranchesInputHandler(self, args.get("selected_index", 0))
            return self._input_handler
        return None


def toggle_ref(refs: list[str], ref: str) -> list[str]:
    return [r for r in refs if r != ref] if ref in refs else refs + [ref]


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


class GraphBranchesInputHandler(ListInputHandler):
    def __init__(self, command: gs_log_graph_edit_branches, selected_index: int = 0) -> None:
        self.command = command
        self.selected_index = selected_index
        self.branches: dict[str, Branch] = {}
        self.refs: list[str] = []
        self.choices: list[str] = []
        self.subset: list[str] = []
        self.keep_open = False

    def name(self) -> str:
        return "branch"

    def list_items(self) -> tuple[list[sublime.ListInputItem], int]:
        branches = sorted(
            self.command.get_branches(),
            key=lambda b: (-b.committerdate, b.canonical_name, b.is_remote)
        )
        self.branches = {branch_ref(branch): branch for branch in branches}
        settings = self.command.view.settings()
        self.subset = list(dict.fromkeys(
            self.resolve_branch(ref) for ref in (settings.get("git_savvy.log_graph_view.branches") or [])
        ))
        self.choices = list(dict.fromkeys(
            self.resolve_branch(ref) for ref in [
                *settings.get("git_savvy.log_graph_view.branch_choices", []), *self.subset
            ]
        ))
        self.refs = self.ordered_refs(self.choices)
        upstreams = self.upstreams_for(self.subset)
        items = [self.branch_item(ref, upstreams) for ref in self.refs]
        if not items:
            items.append(sublime.ListInputItem("There are no branches available.", None))
        return items, self.selected_index

    def preview(self, value: str | None) -> str:
        action = "remove" if value in self.choices else "add"
        return f"[ctrl+enter] to {action}, [enter] to also close"

    def want_event(self) -> bool:
        return True

    def validate(self, value: str | None, event: dict | None = None) -> bool:
        return value in self.refs

    def confirm(self, value: str, event: dict) -> None:
        modifiers = event.get("modifier_keys", {})
        self.keep_open = modifiers.get("ctrl", False) or modifiers.get("primary", False)

    def cancel(self) -> None:
        self.command._input_handler = None
        if window := self.command.view.window():
            enqueue_on_ui(window.run_command, "hide_overlay")

    def ordered_refs(self, selected: list[str]) -> list[str]:
        refs = (["HEAD"] if selected else []) + list(self.branches) + [
            ref for ref in selected if ref != "HEAD" and ref not in self.branches
        ]
        return sorted(refs, key=lambda ref: ref not in selected)

    def upstreams_for(self, selected: list[str]) -> list[str]:
        upstreams = []
        for ref in selected:
            branch = (
                next((branch for branch in self.branches.values() if branch.active), None)
                if ref == "HEAD" else self.branches.get(ref)
            )
            if branch and (upstream := branch.upstream) and upstream.status != "gone":
                namespace = "remotes" if upstream.remote not in ("", ".") else "heads"
                upstream_ref = f"refs/{namespace}/{upstream.canonical_name}"
                if upstream_ref not in selected and upstream_ref not in upstreams:
                    upstreams.append(upstream_ref)
        return upstreams

    def resolve_branch(self, ref: str) -> str:
        if ref == "HEAD" or ref in self.branches:
            return ref
        # Prefer a local branch when a short name also names a remote branch.
        for namespace in ("heads", "remotes"):
            full_ref = f"refs/{namespace}/{ref}"
            if full_ref in self.branches:
                return full_ref
        return ref

    def branch_item(self, ref: str, upstreams: list[str]) -> sublime.ListInputItem:
        branch = self.branches.get(ref)
        name = branch.canonical_name if branch else ref
        return sublime.ListInputItem(
            (
                f"✓ {name}" if ref in self.choices else
                f"  {name}" if self.choices else
                name
            ),
            ref,
            annotation=" · ".join(filter_((
                "upstream" if ref in upstreams else "",
                "remote" if branch and branch.is_remote else "",
                branch.relative_committerdate if branch else ""
            )))
        )
