import re
import sublime


ANSI_ESCAPE_RE = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')


def normalize(string):
    # type: (str) -> str
    return ANSI_ESCAPE_RE.sub('', string.replace('\r\n', '\n').replace('\r', '\n'))


def panel(message):
    # type: (str) -> None
    message = normalize(str(message))
    view = sublime.active_window().active_view()
    view.run_command("gs_display_panel", {"msg": message})


def panel_append(message):
    # type: (str) -> None
    message = normalize(str(message))
    view = sublime.active_window().active_view()
    view.run_command("gs_append_panel", {"msg": message})
