from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from itertools import count
import os
import subprocess
import threading
import time
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

import sublime

from .runtime import enqueue_on_ui
from .utils import abort_proc, proc_has_been_aborted_by_user
from .view import replace_view_content


PANEL_NAME = "GitSavvy Command Log"
PANEL_SETTING = "git_savvy.command_log_panel"
ANNOTATION_KEY = "git_savvy.command_log.abort"
FINISHED_COMMAND_LIMIT = 15
SHOW_RUNNING_AFTER_MS = 100
ANIMATE_RUNNING_AFTER_SECONDS = 1.0
ANIMATION_INTERVAL_MS = 100
GROUP_PREFIX = "             -- "
COMMAND_PREFIX = "{} ({}) "


@dataclass
class Command:
    execution_id: int
    working_dir: str
    command: str
    thread_name: str
    started_at: float
    proc: Optional[subprocess.Popen]
    finished_at: Optional[float] = None
    aborted: bool = False
    shown_while_running: bool = False

    @property
    def is_running(self) -> bool:
        return self.finished_at is None


class CommandSnapshot(NamedTuple):
    execution_id: int
    working_dir: str
    command: str
    thread_name: str
    started_at: float
    finished_at: Optional[float]
    aborted: bool

    @property
    def is_running(self) -> bool:
        return self.finished_at is None


class Annotation(NamedTuple):
    execution_id: int
    start: int
    end: int


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

        command.finished_at = finished_at
        command.aborted = aborted
        command.proc = None
        self._trim_finished_commands()

    def reveal(self, execution_id: int) -> bool:
        command = self.commands.get(execution_id)
        if not command or not command.is_running:
            return False

        command.shown_while_running = True
        return True

    def reveal_all_running(self) -> None:
        for command in self.commands.values():
            if command.is_running:
                command.shown_while_running = True

    def snapshots_for_render(self) -> List[CommandSnapshot]:
        return [
            snapshot(command)
            for command in self.commands.values()
            if not command.is_running or command.shown_while_running
        ]

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
_render_due_at: Dict[int, float] = {}
_rendered_panels: Dict[int, Tuple[sublime.View, str, List[Annotation]]] = {}


def start_command(
    working_dir: str,
    command: str,
    proc: subprocess.Popen
) -> int:
    execution_id = next(_next_execution_id)
    actual_working_dir = os.path.abspath(os.fspath(working_dir))
    with _lock:
        _commands.start(
            execution_id,
            actual_working_dir,
            command,
            threading.current_thread().name[:1],
            proc,
            time.monotonic()
        )

    sublime.set_timeout(
        partial(_reveal_running_command, execution_id),
        SHOW_RUNNING_AFTER_MS
    )
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
        _commands.finish(execution_id, time.monotonic(), aborted)

    enqueue_on_ui(_render_visible_panels)


def prepare_panel(window: sublime.Window) -> None:
    def create_if_missing() -> None:
        if window.is_valid() and not window.find_output_panel(PANEL_NAME):
            ensure_panel(window)

    enqueue_on_ui(create_if_missing)


def show(window: sublime.Window) -> None:
    panel = ensure_panel(window)
    window.run_command("show_panel", {"panel": "output.{}".format(PANEL_NAME)})
    panel_opened()
    panel.show(0)


def panel_opened() -> None:
    with _lock:
        _commands.reveal_all_running()

    _render_visible_panels()


def abort_command(execution_id: int) -> None:
    with _lock:
        proc = _commands.running_process(execution_id)

    # Completion can race with the annotation click.  `abort_proc` also safely
    # tolerates a process that exited just before the kill reaches the OS.
    abort_proc(proc)


def window_closed(window_id: int) -> None:
    _render_due_at.pop(window_id, None)
    _rendered_panels.pop(window_id, None)


def render_commands(
    commands: Iterable[CommandSnapshot],
    now: float
) -> Tuple[str, List[Annotation]]:
    grouped: Dict[str, List[CommandSnapshot]] = {}
    for command in commands:
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
            line = "{}{} $ {}\n".format(
                COMMAND_PREFIX.format(">" if command.is_running else " ", command.thread_name),
                _timing_field(command, now),
                command.command
            )
            start = offset
            end = start + len(line) - 1
            chunks.append(line)
            offset += len(line)
            if command.is_running:
                annotations.append(Annotation(command.execution_id, start, end))

    return "".join(chunks), annotations


def snapshot(command: Command) -> CommandSnapshot:
    return CommandSnapshot(
        command.execution_id,
        command.working_dir,
        command.command,
        command.thread_name,
        command.started_at,
        command.finished_at,
        command.aborted
    )


def _render_visible_panels() -> None:
    for window in sublime.windows():
        if _panel_is_visible(window):
            panel = window.find_output_panel(PANEL_NAME)
            if panel:
                _render(window.id(), panel)


def _render(window_id: int, panel: sublime.View) -> None:
    with _lock:
        commands = _commands.snapshots_for_render()

    now = time.monotonic()
    text, annotations = render_commands(commands, now)
    previous = _rendered_panels.get(window_id)
    if previous and previous[0] == panel and previous[2] == annotations:
        if not _update_panel_text(panel, previous[1], text):
            _draw_abort_annotations(panel, annotations)
    else:
        replace_view_content(panel, text)
        _draw_abort_annotations(panel, annotations)
    _rendered_panels[window_id] = (panel, text, annotations)
    _schedule_next_render(window_id, commands, now)


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


def _schedule_next_render(
    window_id: int,
    commands: List[CommandSnapshot],
    now: float
) -> None:
    running = [command for command in commands if command.is_running]
    if not running:
        _render_due_at.pop(window_id, None)
        return

    elapsed = [max(0.0, now - command.started_at) for command in running]
    delay = (
        ANIMATION_INTERVAL_MS / 1000
        if any(runtime >= ANIMATE_RUNNING_AFTER_SECONDS for runtime in elapsed)
        else min(ANIMATE_RUNNING_AFTER_SECONDS - runtime for runtime in elapsed)
    )
    due_at = now + delay
    scheduled_due_at = _render_due_at.get(window_id)
    if scheduled_due_at is not None and scheduled_due_at <= due_at:
        return

    _render_due_at[window_id] = due_at
    sublime.set_timeout(
        partial(_animate, window_id, due_at),
        max(1, round(delay * 1000))
    )


def _animate(window_id: int, due_at: float) -> None:
    if _render_due_at.get(window_id) != due_at:
        return
    _render_due_at.pop(window_id, None)

    window = next((window for window in sublime.windows() if window.id() == window_id), None)
    if window and _panel_is_visible(window):
        panel = window.find_output_panel(PANEL_NAME)
        if panel:
            _render(window_id, panel)


def _reveal_running_command(execution_id: int) -> None:
    with _lock:
        revealed = _commands.reveal(execution_id)
    if revealed:
        _render_visible_panels()


def ensure_panel(window: sublime.Window) -> sublime.View:
    panel = window.find_output_panel(PANEL_NAME) or window.create_output_panel(PANEL_NAME)
    panel.set_read_only(True)
    panel.set_syntax_file("Packages/GitSavvy/syntax/command_log.sublime-syntax")
    panel.settings().set(PANEL_SETTING, True)
    return panel


def _panel_is_visible(window: sublime.Window) -> bool:
    return window.active_panel() == "output.{}".format(PANEL_NAME)


def _draw_abort_annotations(
    panel: sublime.View,
    annotations: List[Annotation]
) -> None:
    if not annotations:
        panel.erase_regions(ANNOTATION_KEY)
        return

    panel.add_regions(
        ANNOTATION_KEY,
        [sublime.Region(annotation.start, annotation.end) for annotation in annotations],
        annotations=['<a href="{}">Abort</a>'.format(annotation.execution_id)
                     for annotation in annotations],
        annotation_color="#aaa0",
        on_navigate=lambda href: abort_command(int(href)),
        flags=sublime.RegionFlags.NO_UNDO
    )


def _wait_for_process(execution_id: int, proc: subprocess.Popen) -> None:
    proc.wait()
    finish_command(execution_id, proc_has_been_aborted_by_user(proc))


def _timing_field(command: CommandSnapshot, now: float) -> str:
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
