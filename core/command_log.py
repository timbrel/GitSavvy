from __future__ import annotations

from dataclasses import dataclass, replace
from functools import partial
from itertools import count
import math
import os
import subprocess
import threading
import time
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

import sublime

from . import git_activity
from .runtime import enqueue_on_ui
from .utils import abort_proc, proc_has_been_aborted_by_user
from .view import replace_view_content


PANEL_NAME = "GitSavvy Command Log"
PANEL_SETTING = "git_savvy.command_log_panel"
ANNOTATION_KEY = "git_savvy.command_log.abort"
FINISHED_COMMAND_LIMIT = 15
SHOW_RUNNING_AFTER_SECONDS = 0.1
ANIMATE_RUNNING_AFTER_SECONDS = 1.0
ANIMATION_INTERVAL_SECONDS = 0.1
# Prefer the scope accent unless its contrast is too weak; then use normal text.
MIN_ANNOTATION_CONTRAST = 3.0
GROUP_PREFIX = "             -- "
COMMAND_PREFIX = "{} ({}) "


@dataclass(frozen=True)
class Command:
    execution_id: int
    working_dir: str
    command: str
    thread_name: str
    started_at: float
    proc: Optional[subprocess.Popen]
    finished_at: Optional[float] = None
    aborted: bool = False

    @property
    def is_running(self) -> bool:
        return self.finished_at is None


class Annotation(NamedTuple):
    execution_id: int
    start: int


class PendingRender(NamedTuple):
    due_at: float
    token: object


class CommandLog:
    def __init__(self) -> None:
        self.commands: Dict[int, Command] = {}

    def start(
        self,
        execution_id: int,
        working_dir: str,
        command: str,
        thread_name: str,
        proc: subprocess.Popen,
        started_at: float
    ) -> None:
        self.commands[execution_id] = Command(
            execution_id,
            working_dir,
            command,
            thread_name,
            started_at,
            proc
        )

    def finish(self, execution_id: int, finished_at: float, aborted: bool) -> None:
        command = self.commands.get(execution_id)
        if not command or not command.is_running:
            return

        self.commands[execution_id] = replace(
            command, finished_at=finished_at, aborted=aborted, proc=None
        )
        self._trim_finished_commands()

    def running_process(self, execution_id: int) -> Optional[subprocess.Popen]:
        command = self.commands.get(execution_id)
        return command.proc if command and command.is_running else None

    def _trim_finished_commands(self) -> None:
        finished = sorted(
            (command for command in self.commands.values() if not command.is_running),
            key=lambda command: (command.finished_at or 0, command.execution_id),
            reverse=True
        )
        for command in finished[FINISHED_COMMAND_LIMIT:]:
            del self.commands[command.execution_id]


_lock = threading.Lock()
_next_execution_id = count(1)
_commands = CommandLog()
# Protected by _lock. The UI thread tracks whether any log panel is visible;
# command threads only need that boolean to request a shared redraw.
_has_visible_panel = False
_pending_render: Optional[PendingRender] = None
_prepared_windows: set[int] = set()
# Only the UI thread reads or writes rendered content.
_last_render: Tuple[str, List[Annotation]] = ("", [])
_rendered_panels: set[sublime.View] = set()


def start_command(
    working_dir: str,
    command: str,
    proc: subprocess.Popen,
    *,
    show_spinner: bool = False
) -> int:
    execution_id = next(_next_execution_id)
    actual_working_dir = os.path.abspath(os.fspath(working_dir))
    with _lock:
        started_at = time.monotonic()
        _commands.start(
            execution_id,
            actual_working_dir,
            command,
            threading.current_thread().name[:1],
            proc,
            started_at
        )
        if _has_visible_panel:
            _request_redraw(started_at + SHOW_RUNNING_AFTER_SECONDS)

    if show_spinner:
        git_activity.start(execution_id)
    return execution_id


def finish_when_process_exits(
    execution_id: int,
    proc: subprocess.Popen
) -> None:
    threading.Thread(
        target=_wait_for_process,
        args=(execution_id, proc),
        name="GitSavvyGitProcessWaiter",
        daemon=True
    ).start()


def finish_command(execution_id: int, aborted: bool) -> None:
    with _lock:
        finished_at = time.monotonic()
        _commands.finish(execution_id, finished_at, aborted)
        if _has_visible_panel:
            _request_redraw(finished_at)

    git_activity.finish(execution_id)


def prepare_panel(window: sublime.Window) -> None:
    window_id = window.id()
    with _lock:
        if window_id in _prepared_windows:
            return
        _prepared_windows.add(window_id)

    def create_if_missing() -> None:
        if not window.is_valid():
            window_closed(window_id)
        else:
            if not window.find_output_panel(PANEL_NAME):
                ensure_panel(window)
            panel_visibility_changed()

    enqueue_on_ui(create_if_missing)


def show(window: sublime.Window) -> None:
    panel = ensure_panel(window)
    window.run_command("show_panel", {"panel": "output.{}".format(PANEL_NAME)})
    panel_visibility_changed()
    panel.show(0)


def panel_visibility_changed() -> None:
    """On the UI thread, update visibility and draw newly opened panels."""
    global _has_visible_panel, _pending_render
    visible_panels = _visible_panels()
    _rendered_panels.intersection_update(visible_panels)
    with _lock:
        _has_visible_panel = bool(visible_panels)
        if visible_panels:
            _request_redraw(time.monotonic())
        else:
            _pending_render = None


def abort_command(execution_id: int) -> None:
    with _lock:
        proc = _commands.running_process(execution_id)

    # Completion can race with the annotation click.  `abort_proc` also safely
    # tolerates a process that exited just before the kill reaches the OS.
    abort_proc(proc)


def window_closed(window_id: int) -> None:
    global _has_visible_panel, _pending_render
    # on_pre_close_window runs while the closing window is still valid.
    visible_panels = _visible_panels(exclude_window_id=window_id)
    with _lock:
        _has_visible_panel = bool(visible_panels)
        if not visible_panels:
            _pending_render = None
        _prepared_windows.discard(window_id)
    _rendered_panels.intersection_update(visible_panels)


def render_commands(
    commands: Iterable[Command],
    now: float
) -> Tuple[str, List[Annotation]]:
    grouped: Dict[str, List[Command]] = {}
    for command in commands:
        if command.is_running and now < command.started_at + SHOW_RUNNING_AFTER_SECONDS:
            continue
        grouped.setdefault(command.working_dir, []).append(command)

    groups = sorted(
        grouped.items(),
        key=lambda item: max(command.execution_id for command in item[1]),
        reverse=True
    )
    chunks: List[str] = []
    annotations: List[Annotation] = []
    offset = 0
    for group_index, (working_dir, group_commands) in enumerate(groups):
        if group_index:
            chunks.append("\n")
            offset += 1

        heading = "{}{} --\n".format(GROUP_PREFIX, working_dir)
        chunks.append(heading)
        offset += len(heading)
        for command in sorted(
            group_commands,
            key=lambda command: command.execution_id,
            reverse=True
        ):
            show_abort = command.is_running and now - command.started_at >= ANIMATE_RUNNING_AFTER_SECONDS
            line = "{}{} $ {}\n".format(
                COMMAND_PREFIX.format(">" if show_abort else " ", command.thread_name),
                _timing_field(command, now),
                command.command
            )
            start = offset
            chunks.append(line)
            offset += len(line)
            if show_abort:
                annotations.append(Annotation(command.execution_id, start))

    return "".join(chunks), annotations


def _render() -> None:
    global _has_visible_panel, _last_render, _rendered_panels
    visible_panels = _visible_panels()
    _rendered_panels.intersection_update(visible_panels)
    with _lock:
        _has_visible_panel = bool(visible_panels)
        if not visible_panels:
            return
        commands = list(_commands.commands.values())

    now = time.monotonic()
    text, annotations = render_commands(commands, now)
    previous_text, previous_annotations = _last_render
    for panel in visible_panels:
        if panel in _rendered_panels and previous_annotations == annotations:
            if not _update_panel_text(panel, previous_text, text):
                _draw_abort_annotations(panel, annotations)
        else:
            replace_view_content(panel, text)
            _draw_abort_annotations(panel, annotations)
    _last_render = (text, annotations)
    _rendered_panels = set(visible_panels)

    due_at = _next_render_at(commands, now)
    if due_at is not None:
        with _lock:
            if _has_visible_panel:
                _request_redraw(due_at)


def _update_panel_text(panel: sublime.View, previous: str, text: str) -> bool:
    # Recreating annotations or replacing their whole lines interrupts clicks.
    # Timer ticks only need small, same-width edits within existing lines.
    if previous == text:
        return True
    old_lines = previous.splitlines(keepends=True)
    new_lines = text.splitlines(keepends=True)
    if len(old_lines) != len(new_lines) or any(
        len(old) != len(new) for old, new in zip(old_lines, new_lines)
    ):
        replace_view_content(panel, text)
        return False

    offset = 0
    for old, new in zip(old_lines, new_lines):
        if old != new:
            start = next(i for i, (a, b) in enumerate(zip(old, new)) if a != b)
            end = len(old)
            while old[end - 1] == new[end - 1]:
                end -= 1
            replace_view_content(panel, new[start:end], sublime.Region(offset + start, offset + end))
        offset += len(old)
    return True


def _next_render_at(commands: Iterable[Command], now: float) -> Optional[float]:
    deadlines = []
    for command in commands:
        if not command.is_running:
            continue
        if now < command.started_at + SHOW_RUNNING_AFTER_SECONDS:
            due_at = command.started_at + SHOW_RUNNING_AFTER_SECONDS
        elif now < command.started_at + ANIMATE_RUNNING_AFTER_SECONDS:
            due_at = command.started_at + ANIMATE_RUNNING_AFTER_SECONDS
        else:
            due_at = now + ANIMATION_INTERVAL_SECONDS
        deadlines.append(due_at)
    return min(deadlines, default=None)


def _request_redraw(due_at: float) -> None:
    # Caller holds _lock. An earlier paint will recompute the next deadline
    # from the latest command history, so later requests need no callback.
    global _pending_render
    if _pending_render is not None and _pending_render.due_at <= due_at:
        return

    token = object()
    _pending_render = PendingRender(due_at, token)
    sublime.set_timeout(
        partial(_redraw, token),
        max(0, math.ceil((due_at - time.monotonic()) * 1000))
    )


def _redraw(token: object) -> None:
    global _pending_render
    with _lock:
        if _pending_render is None or _pending_render.token is not token:
            return
        _pending_render = None

    _render()


def ensure_panel(window: sublime.Window) -> sublime.View:
    panel = window.find_output_panel(PANEL_NAME) or window.create_output_panel(PANEL_NAME)
    panel.set_read_only(True)
    panel.set_syntax_file("Packages/GitSavvy/syntax/command_log.sublime-syntax")
    panel.settings().set(PANEL_SETTING, True)
    return panel


def _visible_panels(exclude_window_id: Optional[int] = None) -> List[sublime.View]:
    return [
        panel
        for window in sublime.windows()
        if window.id() != exclude_window_id
        if window.active_panel() == "output.{}".format(PANEL_NAME)
        if (panel := window.find_output_panel(PANEL_NAME))
    ]


def _draw_abort_annotations(
    panel: sublime.View,
    annotations: List[Annotation]
) -> None:
    if not annotations:
        panel.erase_regions(ANNOTATION_KEY)
        return

    style = panel.style_for_scope(
        "source.git-savvy.command-log constant.other.command-timing.git-savvy"
    )
    panel_style = panel.style()
    color = _annotation_text_color(style, panel_style)
    link_style = (
        "display: inline-block; padding: 1px 0.6em; "
        "color: {}; background-color: {}; text-decoration: none"
    ).format(color, panel_style["background"])
    panel.add_regions(
        ANNOTATION_KEY,
        [sublime.Region(annotation.start) for annotation in annotations],
        annotations=[
            '<a href="{}" style="{}">Abort</a>'.format(annotation.execution_id, link_style)
            for annotation in annotations
        ],
        annotation_color="#aaa0",
        on_navigate=lambda href: abort_command(int(href)),
        flags=sublime.RegionFlags.NO_UNDO
    )


def _annotation_text_color(scope_style: dict, panel_style: dict) -> str:
    """Choose the scope color most legible as text on the panel background."""
    background = panel_style["background"]
    base = _luminance(background)

    def contrast(color: str) -> float:
        luminance = _luminance(color, background)
        return (max(base, luminance) + 0.05) / (min(base, luminance) + 0.05)

    candidates = [scope_style["foreground"]]
    if scope_style.get("background"):
        candidates.append(scope_style["background"])
    color = max(candidates, key=contrast)
    return color if contrast(color) >= MIN_ANNOTATION_CONTRAST else panel_style["foreground"]


def _luminance(color: str, background: str = "#000000") -> float:
    # Sublime resolves scheme colors to hex RGB(A). Composite translucent scope
    # colors against the panel before comparing their contrast.
    channels = bytes.fromhex(color.lstrip("#"))
    rgb = [channel / 255 for channel in channels[:3]]
    if len(channels) == 4:
        alpha = channels[3] / 255
        backdrop = bytes.fromhex(background.lstrip("#"))
        rgb = [c * alpha + b / 255 * (1 - alpha) for c, b in zip(rgb, backdrop)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def _wait_for_process(execution_id: int, proc: subprocess.Popen) -> None:
    proc.wait()
    finish_command(execution_id, proc_has_been_aborted_by_user(proc))


def _timing_field(command: Command, now: float) -> str:
    if command.aborted:
        return "[xxxxx]"

    ended_at = command.finished_at if command.finished_at is not None else now
    elapsed = max(0.0, ended_at - command.started_at)
    if command.is_running and elapsed < ANIMATE_RUNNING_AFTER_SECONDS:
        return "[     ]"
    if elapsed < 1:
        return "[{:3d}ms]".format(round(elapsed * 1000))
    if elapsed < 10:
        return "[{:4.1f}s]".format(elapsed)
    return "[{:4d}s]".format(round(elapsed))
