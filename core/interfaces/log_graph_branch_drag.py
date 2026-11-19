"""Drag local branch labels to another commit in the graph.

Lifecycle: armed -> dragging -> confirming -> pending -> cleanup.
Arming preserves the whole branch label until a hold or line crossing starts
the drag. Dropping asks for confirmation before starting the Git operation.
Before pending, cancellation restores the transient buffer edits. Pending
ignores cancellation and suppresses graph refreshes: checkout recovery may
change focus, but the Git operation still owns completion. Failure restores
the buffer; success leaves the preview for the operation's refresh to replace.

Session tokens reject callbacks from earlier drags. The recorded buffer version
distinguishes our preview edits from external edits that invalidate the drag.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Literal
from typing_extensions import TypeAlias
from uuid import uuid4

import sublime
from sublime_plugin import EventListener

from ..commands.branch import gs_move_branch
from ..commands.checkout import gs_checkout_new_branch, other_worktree

from ..base_commands import GsTextCommand
from ..git_mixins.branches import Branch
from .log_graph_helper import COMMIT_LINE
from .multi_selector import GRAPH_MOUSE_TARGET_KEY, clear_multiselect
from ..types import FullHash
from ..view import replace_view_content


__all__ = (
    "gs_log_graph_branch_drag",
    "GsLogGraphBranchDragListener"
)


HOLD_MS = 400
SOURCE_KEY = "git_savvy.graph_branch_drag_source"
PREVIEW_KEY = "git_savvy.graph_branch_drag_preview"
DESTINATION_KEY = "git_savvy.graph_branch_drag_destination"
REGION_FLAGS = sublime.HIDDEN | sublime.RegionFlags.NO_UNDO


DragAction: TypeAlias = Literal["start", "activate", "update", "finish", "confirm", "cancel"]


@dataclass(frozen=True)
class Preview:
    original: str
    rendered: str
    cursor: int
    is_source: bool


@dataclass(frozen=True)
class ArmedDrag:
    token: str
    region: sublime.Region
    source: str
    version: int
    phase: Literal["armed"] = "armed"


@dataclass
class ActiveDrag:
    token: str
    branch: str
    previous_tip: FullHash
    checked_out_here: bool
    source: str
    target: str | None
    version: int
    preview: Preview | None = None
    phase: Literal["dragging", "confirming", "pending"] = "dragging"


DragState: TypeAlias = "ArmedDrag | ActiveDrag"

_DRAGS: dict[sublime.View, DragState] = globals().get("_DRAGS", {})
_UPDATING: set[sublime.View] = globals().get("_UPDATING", set())


def get_drag_state(view: sublime.View) -> DragState | None:
    return _DRAGS.get(view)


def get_drag_state_for_token(view: sublime.View, token: str) -> DragState | None:
    state = get_drag_state(view)
    if state and token == state.token:
        return state
    return None


class gs_log_graph_branch_drag(GsTextCommand):
    def run(
        self, edit: sublime.Edit, event: dict | None = None,
        action: DragAction = "start", point: int | None = None, token: str | None = None
    ) -> None:
        state = (
            get_drag_state_for_token(self.view, token) if token
            else get_drag_state(self.view)
        )
        if action == "start":
            if not state and event:
                self.start(event)
        elif not state:
            return
        elif state.phase == "armed":
            self.run_armed_action(state, action, point)
        elif state.phase in {"dragging", "confirming"}:
            self.run_drag_action(state, action, point, token)
        elif state.phase == "pending":
            return
        else:
            raise AssertionError("unknown branch drag phase: {}".format(state.phase))

    def want_event(self) -> bool:
        return True

    def start(self, event: dict) -> None:
        view = self.view
        window = view.window()
        assert window

        view.settings().erase(GRAPH_MOUSE_TARGET_KEY)
        point = view.window_to_text((event["x"], event["y"]))
        region = branch_region_at(view, point)
        match = COMMIT_LINE.search(view.substr(view.line(point)))
        if not region or not match:
            version = view.change_count()
            sublime.set_timeout(
                lambda: start_native_selection_if_unchanged(view, event, version)
            )
            return

        window.focus_view(view)
        branch = self.draggable_branch(region)
        if not branch:
            with updating(view):
                view.sel().clear()
                view.sel().add(region)
            return

        state = ArmedDrag(
            token=uuid4().hex,
            region=region,
            source=match.group("commit_hash"),
            version=view.change_count()
        )
        _DRAGS[view] = state
        with updating(view):
            view.sel().clear()
            view.sel().add(region)

        # Keep the branch selected as a label. Holding, or crossing a commit
        # boundary, turns the selection into a drag.
        sublime.set_timeout(lambda: activate_held_drag(view, state.token), HOLD_MS)
        sublime.set_timeout(
            lambda: start_native_selection_for_drag(view, event, state.token)
        )

    def activate(self, armed: ArmedDrag, point: int | None) -> None:
        view = self.view
        region = armed.region
        branch = self.draggable_branch(region)
        if not branch:
            _DRAGS.pop(view, None)
            return

        state = ActiveDrag(
            token=armed.token,
            branch=branch.name,
            previous_tip=branch.commit_hash,
            checked_out_here=branch.active,
            source=armed.source,
            target=armed.source,
            version=view.change_count()
        )
        _DRAGS[view] = state
        drag = BranchDrag(view, state)
        with updating(view):
            if point is not None:
                drag.track(DESTINATION_KEY, view.line(point))
            drag.begin(region)
            destinations = view.get_regions(DESTINATION_KEY)
            view.erase_regions(DESTINATION_KEY)
            if destinations:
                drag.update(destinations[0].a)

    def run_armed_action(
        self, state: ArmedDrag, action: DragAction, point: int | None
    ) -> None:
        if action in {"cancel", "finish"} or self.view.change_count() != state.version:
            _DRAGS.pop(self.view, None)
        elif action == "activate":
            self.activate(state, point)

    def run_drag_action(
        self, state: ActiveDrag, action: DragAction, point: int | None, token: str | None
    ) -> None:
        drag = BranchDrag(self.view, state)
        if action == "cancel" or self.view.change_count() != state.version:
            drag.cancel()
        elif action == "update" and state.phase == "dragging" and point is not None:
            drag.update(point)
        elif action == "finish" and state.phase == "dragging":
            drag.request_confirmation()
        elif action == "confirm" and state.phase == "confirming" and token:
            drag.apply_move()

    def draggable_branch(self, region: sublime.Region) -> Branch | None:
        branches = self.current_state().get("branches", [])
        if not branches:
            branches = self.get_branches()
        name = self.view.substr(region)
        branch = next((
            branch for branch in branches
            if not branch.is_remote and branch.name == name
        ), None)
        if not branch or other_worktree(branch, self.repo_path):
            return None
        return branch


class GsLogGraphBranchDragListener(EventListener):
    def on_query_context(
        self, view: sublime.View, key: str, operator: int, operand: object, match_all: bool
    ) -> bool | None:
        if key != "git_savvy.graph_branch_drag_active":
            return None
        state = get_drag_state(view)
        active = bool(state and state.phase in {"dragging", "confirming"})
        if operator == sublime.OP_EQUAL:
            return active == operand
        if operator == sublime.OP_NOT_EQUAL:
            return active != operand
        return None

    def on_selection_modified(self, view: sublime.View) -> None:
        state = get_drag_state(view)
        if (
            not state or state.phase in {"confirming", "pending"}
            or view in _UPDATING or not view.sel()
        ):
            return

        selection = view.sel()[-1]
        if state.phase == "armed":
            if view.line(selection.b) != view.line(state.region.a):
                view.run_command("gs_log_graph_branch_drag", {
                    "action": "activate", "point": selection.b, "token": state.token
                })
            elif list(view.sel()) != [state.region]:
                with updating(view):
                    view.sel().clear()
                    view.sel().add(state.region)
            return

        drag = BranchDrag(view, state)
        target, cursor = drag.destination_at(selection.b)
        if list(view.sel()) == [sublime.Region(cursor)] and state.target == target:
            # Native tracking normally extends the selection before we collapse
            # it. A notification with the cursor already normalized is a possible
            # release instead; defer to let a normal finish or more tracking win.
            sublime.set_timeout(lambda: finish_after_native_release(view, state.token))
            return

        # Synchronous normalization avoids painting a native text selection.
        view.run_command("gs_log_graph_branch_drag", {
            "action": "update", "point": selection.b, "token": state.token
        })

    def on_modified(self, view: sublime.View) -> None:
        state = get_drag_state(view)
        # Sublime can deliver our own edit notification after the TextCommand
        # returns, when the synchronous update guard is already gone.
        if (
            state and view not in _UPDATING
            and view.change_count() != state.version
        ):
            cancel_branch_drag(view)

    def on_text_command(
        self, view: sublime.View, command: str, args: dict | None
    ) -> tuple[str, dict] | None:
        state = get_drag_state(view)
        if state and state.phase == "pending" and command == "gs_log_graph_refresh":
            # Focus changes during checkout recovery must not repaint the old
            # position. The operation requests its own refresh on success.
            return "noop", {}
        if view in _UPDATING:
            return None
        if command in {"gs_log_graph_refresh", "gs_log_graph_multiselect", "gs_clear_multiselect"}:
            cancel_branch_drag(view)
        elif command == "gs_log_graph_mouse" and not (args or {}).get("release"):
            cancel_branch_drag(view)
        elif state and state.phase == "confirming" and command == "drag_select":
            # Clicking outside the popup can start native selection before
            # its dismissal callback arrives, bypassing our mouse binding.
            cancel_branch_drag(view)
        return None

    def on_deactivated(self, view: sublime.View) -> None:
        cancel_branch_drag(view)

    def on_pre_close(self, view: sublime.View) -> None:
        cancel_branch_drag(view)


class BranchDrag:
    """Manage reversible preview edits using regions that track buffer shifts.

    The source label is replaced by a "|" marker. A destination preview replaces
    a whole commit line, except at the source where it replaces just the marker.
    Preview stores the replaced text and its rendered form so restoration only
    undoes text we still recognize as ours. SOURCE_KEY follows the marker (or
    the label while previewing the source); PREVIEW_KEY follows the replacement.
    DESTINATION_KEY keeps the hovered line anchored while earlier edits are undone.
    """

    def __init__(self, view: sublime.View, state: ActiveDrag) -> None:
        self.view = view
        self.state = state

    def begin(self, region: sublime.Region) -> None:
        view = self.view
        with updating(view):
            # A selected branch would otherwise start native text drag-and-drop.
            self.place_cursor(region.a)
            # Saved selections use fixed offsets; preview edits would invalidate them.
            # Call the helper, not the command which would cancel this drag.
            clear_multiselect(view)
            replace_view_content(view, "|", region)
            self.track(SOURCE_KEY, sublime.Region(region.a, region.a + 1))
            self.place_cursor(region.a + 1)
            self.record_buffer_version()

    def update(self, point: int) -> None:
        view = self.view
        target, cursor = self.destination_at(point)
        with updating(view):
            if target != self.state.target:
                # Track the hovered line while undoing the previous preview.
                self.track(DESTINATION_KEY, view.line(point))
                self.restore_preview()
                destinations = view.get_regions(DESTINATION_KEY)
                if destinations:
                    destination = destinations[0]
                    if target:
                        cursor = self.preview_destination(destination)
                    else:
                        cursor = destination.a
                view.erase_regions(DESTINATION_KEY)
            self.state.target = target
            self.place_cursor(cursor)
            self.record_buffer_version()

    def request_confirmation(self) -> None:
        if not self.state.target or self.state.target == self.state.source:
            self.cancel()
            return
        self.state.phase = "confirming"
        self.record_buffer_version()
        view, token = self.view, self.state.token

        def on_done(index: int) -> None:
            if view.is_valid():
                view.run_command("gs_log_graph_branch_drag", {
                    "action": "confirm" if index == 0 else "cancel", "token": token
                })

        view.show_popup_menu(
            ["Move '{}' here".format(self.state.branch)],
            on_done, view.sel()[0].b if view.sel() else -1
        )

    def apply_move(self) -> None:
        view, state = self.view, self.state
        window = view.window()
        if not state.target or not window:
            self.cancel()
            return
        state.phase = "pending"
        self.record_buffer_version()
        if state.checked_out_here:
            # Checkout may open a dirty-worktree recovery prompt.
            gs_checkout_new_branch(window).checkout(
                branch_name=state.branch, start_point=state.target, force=True,
                on_done=self.on_move_done
            )
        else:
            gs_move_branch(window).move(
                branch=state.branch, target=state.target,
                previous_tip=state.previous_tip, undo_owner=view.id(),
                on_done=self.on_move_done
            )

    def on_move_done(self, did_move: bool) -> None:
        view = self.view
        if not view.is_valid():
            _DRAGS.pop(view, None)
            return
        state = get_drag_state_for_token(view, self.state.token)
        if not state or state.phase != "pending":
            return
        if did_move:
            # Keep the preview in place until the graph refreshes.
            self.clear_tracking()
        else:
            self.cancel()

    def cancel(self) -> None:
        view, state = self.view, self.state
        _DRAGS.pop(view, None)
        with updating(view):
            self.restore_preview()
            source = self.source_region()
            if source and view.substr(source) == "|":
                replace_view_content(view, state.branch, source)
            self.clear_tracking()

    def clear_tracking(self) -> None:
        state = get_drag_state_for_token(self.view, self.state.token)
        if state:
            _DRAGS.pop(self.view, None)
        for key in (SOURCE_KEY, PREVIEW_KEY, DESTINATION_KEY):
            self.view.erase_regions(key)

    def destination_at(self, point: int) -> tuple[str | None, int]:
        view = self.view
        line = view.line(point)
        match = COMMIT_LINE.search(view.substr(line))
        if not match:
            return None, line.a
        preview, regions = self.state.preview, view.get_regions(PREVIEW_KEY)
        if preview and regions and line.contains(regions[0]):
            cursor = regions[0].a + preview.cursor
        elif (source := self.source_region()) and line.contains(source):
            cursor = source.b
        else:
            cursor = line.a + match.end("commit_hash") + 1
        return match.group("commit_hash"), cursor

    def preview_destination(self, line: sublime.Region) -> int:
        view, branch = self.view, self.state.branch
        original = view.substr(line)
        match = COMMIT_LINE.search(original)
        if not match:
            return line.a
        source = self.source_region()
        is_source = bool(source and line.contains(source))
        if is_source:
            assert source
            region = source
            original = "|"
            rendered = branch
            cursor = len(branch)
        else:
            region = line
            if match.group("decoration"):
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
        self.state.preview = Preview(
            original=original,
            rendered=rendered,
            cursor=cursor,
            is_source=is_source
        )
        return region.a + cursor

    def restore_preview(self) -> None:
        view, preview = self.view, self.state.preview
        regions = view.get_regions(PREVIEW_KEY)
        if preview and regions and view.substr(regions[0]) == preview.rendered:
            region = regions[0]
            replace_view_content(view, preview.original, region)
            if preview.is_source:
                self.track(SOURCE_KEY, sublime.Region(region.a, region.a + 1))
        view.erase_regions(PREVIEW_KEY)
        self.state.preview = None

    def source_region(self) -> sublime.Region | None:
        """Return the marker only while it still belongs to the source commit."""
        view = self.view
        regions = view.get_regions(SOURCE_KEY)
        if not regions:
            return None
        region = regions[0]
        line = view.line(region.a)
        match = COMMIT_LINE.search(view.substr(line))
        if (
            match and match.group("commit_hash") == self.state.source
            and match.group("decoration")
            and line.a + match.start("decoration") <= region.a
            and region.b <= line.a + match.end("decoration")
            and view.substr(region) in ("|", self.state.branch)
        ):
            return region
        return None

    def place_cursor(self, point: int) -> None:
        if list(self.view.sel()) != [sublime.Region(point)]:
            self.view.sel().clear()
            self.view.sel().add(sublime.Region(point))

    def track(self, key: str, region: sublime.Region) -> None:
        self.view.add_regions(key, [region], flags=REGION_FLAGS)

    def record_buffer_version(self) -> None:
        self.state.version = self.view.change_count()


def cancel_branch_drag(view: sublime.View) -> None:
    if not get_drag_state(view):
        return
    if view.is_valid():
        view.run_command("gs_log_graph_branch_drag", {"action": "cancel"})
    else:
        _DRAGS.pop(view, None)


def reset_branch_drags() -> None:
    """Cancel all drags and restore their transient buffer edits."""
    for view, state in list(_DRAGS.items()):
        if state.phase == "armed":
            _DRAGS.pop(view, None)
        else:
            BranchDrag(view, state).cancel()
    _UPDATING.clear()


def branch_region_at(view: sublime.View, point: int) -> sublime.Region | None:
    return next((
        region for region in view.find_by_selector(
            "meta.graph.branch.git-savvy constant.other.git.branch"
        )
        if region.a <= point < region.b
    ), None)


def start_native_selection_if_unchanged(
    view: sublime.View, event: dict, version: int
) -> None:
    if not get_drag_state(view) and view.change_count() == version:
        view.run_command("drag_select", {"event": event, "by": "words"})


def start_native_selection_for_drag(
    view: sublime.View, event: dict, token: str
) -> None:
    state = get_drag_state_for_token(view, token)
    if state and state.phase != "confirming":
        view.run_command("drag_select", {"event": event, "by": "words"})


def activate_held_drag(view: sublime.View, token: str) -> None:
    if view.is_valid():
        view.run_command("gs_log_graph_branch_drag", {"action": "activate", "token": token})


def finish_after_native_release(view: sublime.View, token: str) -> None:
    """Recover a drop when native tracking swallows mouse-up on Windows.

    Recheck on the next tick: a normal finish may have changed phase, or resumed
    native tracking may have extended the selection again. Only an unchanged,
    normalized cursor in the same drag is treated as a release.
    """
    if not view.is_valid():
        return
    state = get_drag_state_for_token(view, token)
    if not state or state.phase != "dragging":
        return
    drag = BranchDrag(view, state)
    if not view.sel():
        return
    target, cursor = drag.destination_at(view.sel()[-1].b)
    if list(view.sel()) == [sublime.Region(cursor)] and state.target == target:
        view.run_command("gs_log_graph_branch_drag", {"action": "finish", "token": token})


@contextmanager
def updating(view: sublime.View) -> Iterator[None]:
    """Suppress native selection normalization and redraw cancellation during edits."""
    was_updating = view in _UPDATING
    _UPDATING.add(view)
    try:
        yield
    finally:
        if not was_updating:
            _UPDATING.discard(view)
