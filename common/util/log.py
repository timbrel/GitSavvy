import re
import sublime


MYPY = False
if MYPY:
    from typing import Optional


PANEL_NAME = "GitSavvy"
ANSI_ESCAPE_RE = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')


def normalize(string):
    # type: (str) -> str
    return ANSI_ESCAPE_RE.sub('', string.replace('\r\n', '\n').replace('\r', '\n'))


def display_panel(window, message):
    # type: (sublime.Window, str) -> sublime.View
    panel_view = create_panel(window)
    append_to_panel(panel_view, message)
    panel_view.show(0)
    show_panel(window)
    return panel_view


def ensure_panel(window):
    # type: (sublime.Window) -> sublime.View
    return get_panel(window) or create_panel(window)


def get_panel(window):
    # type: (sublime.Window) -> Optional[sublime.View]
    return window.find_output_panel(PANEL_NAME)


def create_panel(window):
    # type: (sublime.Window) -> sublime.View
    return window.create_output_panel(PANEL_NAME)


def show_panel(window):
    # type: (sublime.Window) -> None
    window.run_command("show_panel", {"panel": "output.{}".format(PANEL_NAME)})


def append_to_panel(panel, message):
    # type: (sublime.View, str) -> None
    message = normalize(message)
    panel.set_read_only(False)
    panel.run_command('append', {
        'characters': message,
        'force': True,
        'scroll_to_end': True
    })
    panel.set_read_only(True)
