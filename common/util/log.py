import re
import subprocess
import sublime

from GitSavvy.core.view import replace_view_content

from typing import Dict, List, Optional


PANEL_NAME = "GitSavvy"
ABORT_HINT = "\n[ctrl+z to abort]"
ANSI_ESCAPE_RE = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
RUNNING_PROCESSES: Dict[int, List[subprocess.Popen]] = {}


def normalize(string: str) -> str:
    return ANSI_ESCAPE_RE.sub('', string.replace('\r\n', '\n').replace('\r', '\n'))


def init_panel(window: sublime.Window) -> sublime.View:
    panel_view = create_panel(window)
    show_panel(window)
    return panel_view


def display_panel(window: sublime.Window, message: str) -> sublime.View:
    panel_view = init_panel(window)
    append_to_panel(panel_view, message)
    panel_view.show(0)
    return panel_view


def ensure_panel(window: sublime.Window) -> sublime.View:
    return get_panel(window) or create_panel(window)


def get_panel(window: sublime.Window) -> Optional[sublime.View]:
    return window.find_output_panel(PANEL_NAME)


def create_panel(window: sublime.Window) -> sublime.View:
    panel_view = window.create_output_panel(PANEL_NAME)
    panel_view.set_syntax_file("Packages/GitSavvy/syntax/output_panel.sublime-syntax")
    panel_view.settings().set("git_savvy.output_panel", True)
    return panel_view


def show_panel(window: sublime.Window) -> None:
    window.run_command("show_panel", {"panel": "output.{}".format(PANEL_NAME)})


def append_to_panel(panel: sublime.View, message: str) -> None:
    # We support standard progress bars with "\r" line endings!
    # If we see such a line ending, we remember where we started
    # our write as `virtual_cursor` as this is where the next
    # write must begin.
    _erase_abort_hint(panel)
    has_trailing_carriage_return = message.endswith("\r")
    message = normalize(message)

    eof = panel.size()
    cursor = panel.settings().get("virtual_cursor") or eof
    region = sublime.Region(cursor, eof)
    replace_view_content(panel, message, region=region)
    panel.settings().set("virtual_cursor", cursor if has_trailing_carriage_return else None)

    _ensure_abort_hint(panel)
    eof_after_append = panel.size()
    panel.show(eof_after_append)


def start_abortable_command(panel: sublime.View, proc: subprocess.Popen) -> None:
    processes = RUNNING_PROCESSES.setdefault(panel.buffer_id(), [])
    processes.append(proc)
    _ensure_abort_hint(panel)


def finish_abortable_command(panel: sublime.View, proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return

    processes = RUNNING_PROCESSES.get(panel.buffer_id()) or []
    try:
        processes.remove(proc)
    except ValueError:
        pass
    if not processes:
        RUNNING_PROCESSES.pop(panel.buffer_id(), None)


def append_done_message(panel: sublime.View, elapsed: float, status: str = "Done") -> None:
    _erase_abort_hint(panel)
    append_to_panel(panel, "\n[{} in {:.2f}s]".format(status, elapsed))


def running_process_for_panel(panel: sublime.View) -> Optional[subprocess.Popen]:
    processes = RUNNING_PROCESSES.get(panel.buffer_id()) or []
    for proc in reversed(processes):
        if proc.poll() is None:
            return proc
    return None


def panel_can_abort(panel: sublime.View) -> bool:
    return running_process_for_panel(panel) is not None


def _ensure_abort_hint(panel: sublime.View) -> None:
    if not panel_can_abort(panel) or _abort_hint_region(panel):
        return

    eof = panel.size()
    replace_view_content(panel, ABORT_HINT, region=sublime.Region(eof, eof))


def _erase_abort_hint(panel: sublime.View) -> None:
    region = _abort_hint_region(panel)
    if region is not None:
        replace_view_content(panel, "", region=region)


def _abort_hint_region(panel: sublime.View) -> Optional[sublime.Region]:
    eof = panel.size()
    start = eof - len(ABORT_HINT)
    if start >= 0 and panel.substr(sublime.Region(start, eof)) == ABORT_HINT:
        return sublime.Region(start, eof)
    return None
