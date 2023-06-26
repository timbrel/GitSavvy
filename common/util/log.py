import re
import sublime

from GitSavvy.core.view import replace_view_content

from typing import Optional


PANEL_NAME = "GitSavvy"
ANSI_ESCAPE_RE = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')


def normalize(string):
    # type: (str) -> str
    return ANSI_ESCAPE_RE.sub('', string.replace('\r\n', '\n').replace('\r', '\n'))


def init_panel(window):
    # type: (sublime.Window) -> sublime.View
    panel_view = create_panel(window)
    show_panel(window)
    return panel_view


def display_panel(window, message):
    # type: (sublime.Window, str) -> sublime.View
    panel_view = init_panel(window)
    append_to_panel(panel_view, message)
    panel_view.show(0)
    return panel_view


def ensure_panel(window):
    # type: (sublime.Window) -> sublime.View
    return get_panel(window) or create_panel(window)


def get_panel(window):
    # type: (sublime.Window) -> Optional[sublime.View]
    return window.find_output_panel(PANEL_NAME)


def create_panel(window):
    # type: (sublime.Window) -> sublime.View
    panel_view = window.create_output_panel(PANEL_NAME)
    panel_view.set_syntax_file("Packages/GitSavvy/syntax/output_panel.sublime-syntax")
    return panel_view


def show_panel(window):
    # type: (sublime.Window) -> None
    window.run_command("show_panel", {"panel": "output.{}".format(PANEL_NAME)})


def append_to_panel(panel, message):
    # type: (sublime.View, str) -> None
    # We support standard progress bars with "\r" line endings!
    # If we see such a line ending, we remember where we started
    # our write as `virtual_cursor` as this is where the next
    # write must begin.
    has_trailing_carriage_return = message.endswith("\r")
    message = normalize(message)

    eof = panel.size()
    cursor = panel.settings().get("virtual_cursor") or eof
    region = sublime.Region(cursor, eof)
    replace_view_content(panel, message, region=region)
    panel.settings().set("virtual_cursor", cursor if has_trailing_carriage_return else None)

    eof_after_append = panel.size()
    panel.show(eof_after_append)
