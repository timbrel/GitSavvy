"""Local branch moves: idle -> armed -> dragging -> confirming -> idle.

One serialized session owns the source marker and destination preview. All exits
restore those edits before a confirmed move is handed to the branch command.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Literal, TypedDict
from typing_extensions import TypeAlias
from uuid import uuid4

import sublime
from sublime_plugin import EventListener, TextCommand

from ..commands.checkout import other_worktree
from .log_graph_mouse import HOLD_MS
from ..git_command import GitCommand
from .log_graph_helper import COMMIT_LINE
from ..types import FullHash
from ..view import replace_view_content


__all__ = ("gs_log_graph_branch_drag", "GsLogGraphBranchDragListener")

DRAG_KEY = "git_savvy.graph_branch_drag"
UPDATING_KEY = "git_savvy.graph_branch_drag_updating"
SOURCE_KEY = "git_savvy.graph_branch_drag_source"
PREVIEW_KEY = "git_savvy.graph_branch_drag_preview"
DESTINATION_KEY = "git_savvy.graph_branch_drag_destination"
REGION_FLAGS = sublime.HIDDEN | sublime.RegionFlags.NO_UNDO


class gs_log_graph_branch_drag(TextCommand, GitCommand):
    """Translate mouse events and token-checked menu choices into transitions."""

    def run(
        self, edit: sublime.Edit, event: dict | None = None,
        action: str = "start", point: int | None = None, token: str | None = None
    ) -> None:
        view = self.view
        state: DragState | None = view.settings().get(DRAG_KEY)
        if action == "start":
            # Never give a second press ownership of an unfinished preview.
            if state is None and event is not None:
                self.start(event)
            return
        if state is None or (token is not None and token != state["token"]):
            return
        if state["phase"] == "armed":
            if action in {"cancel", "finish"} or view.change_count() != state["version"]:
                view.settings().erase(DRAG_KEY)
            elif action == "activate":
                self.activate(state, point)
            return
        session = BranchDrag(view, state)
        if action == "cancel" or view.change_count() != state["version"]:
            session.close()
        elif action == "update" and state["phase"] == "dragging" and point is not None:
            session.update(point)
        elif action == "finish" and state["phase"] == "dragging":
            session.finish()
        elif action == "confirm" and state["phase"] == "confirming" and token == state["token"]:
            session.close(move=True)

    def want_event(self) -> bool:
        return True

    def start(self, event: dict) -> None:
        view = self.view
        if not view.settings().get("git_savvy.log_graph_view"):
            return
        view.settings().erase("git_savvy.log_graph_mouse_press")
        point = view.window_to_text((event["x"], event["y"]))
        region = branch_region_at(view, point)
        match = COMMIT_LINE.search(view.substr(view.line(point)))
        if region is None or match is None:
            version = view.change_count()
            sublime.set_timeout(lambda: start_native_selection(view, event, version=version))
            return
        window = view.window()
        if window is not None:
            window.focus_view(view)
        # The graph can open before branch metadata has been cached.
        branches = self.current_state().get("branches", []) or self.get_branches()
        branch = next((
            branch for branch in branches
            if not branch.is_remote and branch.name == view.substr(region)
        ), None)
        if branch is None or other_worktree(branch, self.repo_path):
            with updating(view):
                view.sel().clear()
                view.sel().add(region)
            return
        state: ArmedDrag = {
            "token": uuid4().hex, "phase": "armed", "region": [region.a, region.b],
            "source": match.group("commit_hash"), "version": view.change_count()
        }
        with updating(view):
            view.settings().set(DRAG_KEY, state)
            view.sel().clear()
            view.sel().add(view.word(point))
        # A simple double-click makes no buffer edits.
        # Release invalidates both callbacks; moving to another commit activates
        # immediately instead of waiting for the hold threshold.
        sublime.set_timeout(lambda: activate_held_drag(view, state["token"]), HOLD_MS)
        sublime.set_timeout(lambda: start_native_selection(view, event, token=state["token"]))

    def activate(self, armed: ArmedDrag, point: int | None) -> None:
        view = self.view
        region = sublime.Region(*armed["region"])
        branch = next((
            branch for branch in self.get_branches()
            if not branch.is_remote and branch.name == view.substr(region)
        ), None)
        if branch is None or other_worktree(branch, self.repo_path):
            view.settings().erase(DRAG_KEY)
            return
        state: MovingDrag = {
            "token": armed["token"], "phase": "dragging",
            "branch": branch.name, "previous_tip": branch.commit_hash,
            "checked_out_here": branch.active,
            "source": armed["source"], "target": armed["source"],
            "version": view.change_count(), "preview": None
        }
        session = BranchDrag(view, state)
        with updating(view):
            if point is not None:
                session.track(DESTINATION_KEY, view.line(point))
            session.begin(region)
            destinations = view.get_regions(DESTINATION_KEY)
            view.erase_regions(DESTINATION_KEY)
            if destinations:
                session.update(destinations[0].a)


class GsLogGraphBranchDragListener(EventListener):
    """Route native tracking and lifecycle cancellation; own no drag state."""

    def on_query_context(
        self, view: sublime.View, key: str, operator: int, operand: object, match_all: bool
    ) -> bool | None:
        if key != "git_savvy.graph_branch_drag_active":
            return None
        state: DragState | None = view.settings().get(DRAG_KEY)
        active = state is not None and state["phase"] != "armed"
        if operator == sublime.OP_EQUAL:
            return active == operand
        if operator == sublime.OP_NOT_EQUAL:
            return active != operand
        return None

    def on_selection_modified(self, view: sublime.View) -> None:
        state: DragState | None = view.settings().get(DRAG_KEY)
        if (
            state is None or state["phase"] == "confirming"
            or view.settings().get(UPDATING_KEY) or not view.sel()
        ):
            return
        selection = view.sel()[-1]
        if state["phase"] == "armed":
            region = sublime.Region(*state["region"])
            if view.line(selection.b) != view.line(region.a):
                view.run_command("gs_log_graph_branch_drag", {
                    "action": "activate", "point": selection.b, "token": state["token"]
                })
            return
        session = BranchDrag(view, state)
        target, cursor = session.destination_at(selection.b)
        if list(view.sel()) == [sublime.Region(cursor)] and state["target"] == target:
            sublime.set_timeout(lambda: finish_after_native_release(view, state["token"]))
            return
        # Synchronous normalization avoids painting a native text selection.
        view.run_command("gs_log_graph_branch_drag", {
            "action": "update", "point": selection.b, "token": state["token"]
        })

    def on_modified(self, view: sublime.View) -> None:
        state: DragState | None = view.settings().get(DRAG_KEY)
        # Sublime can deliver our own edit notification after the TextCommand
        # returns, when the synchronous update guard is already gone.
        if (
            state is not None and not view.settings().get(UPDATING_KEY)
            and view.change_count() != state["version"]
        ):
            cancel_branch_drag(view)

    def on_text_command(self, view: sublime.View, command: str, args: dict | None) -> None:
        if view.settings().get(UPDATING_KEY):
            return
        if command in {"gs_log_graph_refresh", "gs_log_graph_multiselect", "gs_clear_multiselect"}:
            cancel_branch_drag(view)
        elif command == "gs_log_graph_mouse" and not (args or {}).get("release"):
            cancel_branch_drag(view)

    def on_deactivated(self, view: sublime.View) -> None:
        cancel_branch_drag(view)

    def on_pre_close(self, view: sublime.View) -> None:
        cancel_branch_drag(view)


class BranchDrag:
    """Own transitions and reversible buffer edits for one session."""

    def __init__(self, view: sublime.View, state: MovingDrag) -> None:
        self.view = view
        self.state = state

    def begin(self, region: sublime.Region) -> None:
        view = self.view
        with updating(view):
            # A selected branch would otherwise start native text drag-and-drop.
            self.place_cursor(region.a)
            view.settings().erase("git_savvy.mouse_selected_target")
            replace_view_content(view, "|", region)
            self.track(SOURCE_KEY, sublime.Region(region.a, region.a + 1))
            self.place_cursor(region.a + 1)
            self.save()

    def update(self, point: int) -> None:
        view = self.view
        target, cursor = self.destination_at(point)
        with updating(view):
            if target != self.state["target"]:
                # Track the hovered line while undoing the previous preview.
                self.track(DESTINATION_KEY, view.line(point))
                self.restore_preview()
                destinations = view.get_regions(DESTINATION_KEY)
                if destinations:
                    cursor = (
                        self.preview_destination(destinations[0]) if target else
                        destinations[0].a
                    )
                view.erase_regions(DESTINATION_KEY)
            self.state["target"] = target
            self.place_cursor(cursor)
            self.save()

    def finish(self) -> None:
        if not self.state["target"] or self.state["target"] == self.state["source"]:
            self.close()
            return
        self.state["phase"] = "confirming"
        self.save()
        view, token = self.view, self.state["token"]

        def on_done(index: int) -> None:
            if view.is_valid():
                view.run_command("gs_log_graph_branch_drag", {
                    "action": "confirm" if index == 0 else "cancel", "token": token
                })

        view.show_popup_menu(
            ["Move '{}' here".format(self.state["branch"])],
            on_done, view.sel()[0].b if view.sel() else -1
        )

    def close(self, move: bool = False) -> None:
        view, state = self.view, self.state
        # Invalidate callbacks before restoring text or issuing a Git command.
        view.settings().erase(DRAG_KEY)
        with updating(view):
            self.restore_preview()
            source = self.source_region()
            if source is not None and view.substr(source) == "|":
                replace_view_content(view, state["branch"], source)
            for key in (SOURCE_KEY, PREVIEW_KEY, DESTINATION_KEY):
                view.erase_regions(key)
        if move and state["target"] and (window := view.window()):
            if state["checked_out_here"]:
                # Moving our current branch also updates this checkout, with
                # the existing dirty-worktree warning and --merge recovery.
                window.run_command("gs_checkout_new_branch", {
                    "branch_name": state["branch"], "start_point": state["target"],
                    "force": True
                })
            else:
                # branch --force still protects branches used by other worktrees.
                window.run_command("gs_move_branch", {
                    "branch": state["branch"], "target": state["target"],
                    "previous_tip": state["previous_tip"], "undo_owner": view.id()
                })

    def destination_at(self, point: int) -> tuple[str | None, int]:
        view = self.view
        line = view.line(point)
        match = COMMIT_LINE.search(view.substr(line))
        if match is None:
            return None, line.a
        preview, regions = self.state["preview"], view.get_regions(PREVIEW_KEY)
        if preview and regions and line.contains(regions[0]):
            cursor = regions[0].a + preview["cursor"]
        elif (source := self.source_region()) is not None and line.contains(source):
            cursor = source.b
        else:
            cursor = line.a + match.end("commit_hash") + 1
        return match.group("commit_hash"), cursor

    def preview_destination(self, line: sublime.Region) -> int:
        """Save a reversible decoration edit and return the branch-name endpoint.

        On the source line, expand only its tracked marker back to the name.
        """
        view, branch = self.view, self.state["branch"]
        original = view.substr(line)
        match = COMMIT_LINE.search(original)
        if match is None:
            return line.a
        source = self.source_region()
        is_source = source is not None and line.contains(source)
        if is_source:
            assert source is not None
            region, original, rendered, cursor = source, "|", branch, len(branch)
        else:
            region = line
            if match.group("decoration") is not None:
                offset = match.end("decoration")
                insertion, cursor = ", " + branch, offset + 2 + len(branch)
            else:
                offset = match.end("commit_hash") + 1
                insertion, cursor = "(" + branch + ") ", offset + 1 + len(branch)
            rendered = original[:offset] + insertion + original[offset:]
        replace_view_content(view, rendered, region)
        expanded = sublime.Region(region.a, region.a + len(rendered))
        self.track(PREVIEW_KEY, expanded)
        if is_source:
            self.track(SOURCE_KEY, expanded)
        self.state["preview"] = {
            "original": original, "rendered": rendered, "cursor": cursor, "source": is_source
        }
        return region.a + cursor

    def restore_preview(self) -> None:
        view, preview = self.view, self.state["preview"]
        regions = view.get_regions(PREVIEW_KEY)
        if preview and regions and view.substr(regions[0]) == preview["rendered"]:
            region = regions[0]
            replace_view_content(view, preview["original"], region)
            if preview["source"]:
                self.track(SOURCE_KEY, sublime.Region(region.a, region.a + 1))
        view.erase_regions(PREVIEW_KEY)
        self.state["preview"] = None

    def source_region(self) -> sublime.Region | None:
        """Never restore a stale marker onto unrelated text after a redraw."""
        view = self.view
        regions = view.get_regions(SOURCE_KEY)
        if not regions:
            return None
        region = regions[0]
        line = view.line(region.a)
        match = COMMIT_LINE.search(view.substr(line))
        if (
            match and match.group("commit_hash") == self.state["source"]
            and match.group("decoration") is not None
            and line.a + match.start("decoration") <= region.a
            and region.b <= line.a + match.end("decoration")
            and view.substr(region) in ("|", self.state["branch"])
        ):
            return region
        return None

    def place_cursor(self, point: int) -> None:
        if list(self.view.sel()) != [sublime.Region(point)]:
            self.view.sel().clear()
            self.view.sel().add(sublime.Region(point))

    def track(self, key: str, region: sublime.Region) -> None:
        self.view.add_regions(key, [region], flags=REGION_FLAGS)

    def save(self) -> None:
        self.state["version"] = self.view.change_count()
        self.view.settings().set(DRAG_KEY, self.state)


class ArmedDrag(TypedDict):
    token: str
    phase: Literal["armed"]
    region: list[int]
    source: str
    version: int


class MovingDrag(TypedDict):
    token: str
    phase: Literal["dragging", "confirming"]
    branch: str
    previous_tip: FullHash
    checked_out_here: bool
    source: str
    target: str | None
    version: int
    preview: Preview | None


DragState: TypeAlias = "ArmedDrag | MovingDrag"


class Preview(TypedDict):
    original: str
    rendered: str
    cursor: int
    source: bool


def cancel_branch_drag(view: sublime.View) -> None:
    if view.is_valid() and view.settings().get(DRAG_KEY):
        view.run_command("gs_log_graph_branch_drag", {"action": "cancel"})


def reset_branch_drags() -> None:
    """Restore transient text on package unload/load, including inactive views."""
    for window in sublime.windows():
        for view in window.views():
            state: DragState | None = view.settings().get(DRAG_KEY)
            if state is not None:
                if state["phase"] == "armed":
                    view.settings().erase(DRAG_KEY)
                else:
                    BranchDrag(view, state).close()


def branch_region_at(view: sublime.View, point: int) -> sublime.Region | None:
    return next((
        region for region in view.find_by_selector(
            "meta.graph.branch.git-savvy constant.other.git.branch"
        )
        if region.a <= point < region.b
    ), None)


def start_native_selection(
    view: sublime.View, event: dict, token: str | None = None, version: int | None = None
) -> None:
    """Start tracking only for the captured session or unchanged fallback buffer.

    A token guards a drag session; without one, version guards plain selection.
    """
    if not view.is_valid():
        return
    state: DragState | None = view.settings().get(DRAG_KEY)
    if token is not None:
        if state is None or state["token"] != token or state["phase"] == "confirming":
            return
    elif state is not None or view.change_count() != version:
        return
    view.run_command("drag_select", {"event": event, "by": "words"})


def activate_held_drag(view: sublime.View, token: str) -> None:
    if view.is_valid():
        view.run_command("gs_log_graph_branch_drag", {"action": "activate", "token": token})


def finish_after_native_release(view: sublime.View, token: str) -> None:
    """Finish on left release even when native tracking consumes right-click.

    Observed on Windows: tracking continues but left release then reports the
    normalized selection without dispatching finish. Treat this as a normal
    drop, effectively ignoring the swallowed right-click. Defer so a normal
    finish can take precedence; moving the branch still needs confirmation.
    """
    if not view.is_valid():
        return
    state: DragState | None = view.settings().get(DRAG_KEY)
    if state is None or state["token"] != token or state["phase"] != "dragging":
        return
    session = BranchDrag(view, state)
    if not view.sel():
        return
    target, cursor = session.destination_at(view.sel()[-1].b)
    if list(view.sel()) == [sublime.Region(cursor)] and state["target"] == target:
        view.run_command("gs_log_graph_branch_drag", {"action": "finish", "token": token})


@contextmanager
def updating(view: sublime.View) -> Iterator[None]:
    """Suppress native selection normalization and redraw cancellation during edits."""
    was_updating = view.settings().get(UPDATING_KEY, False)
    view.settings().set(UPDATING_KEY, True)
    try:
        yield
    finally:
        if not was_updating:
            view.settings().erase(UPDATING_KEY)
