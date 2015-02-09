"""
Logging functionality that is intended to be consumed by the user.
"""

import sublime
from sublime_plugin import TextCommand


PANEL_NAME = "GitSavvy"


def panel(*msgs):
    msg = "\n".join(str(msg) for msg in msgs)
    sublime.active_window().active_view().run_command("gs_display_panel", {"msg": msg})


class GsDisplayPanelCommand(TextCommand):

    """
    Given a `msg` string, open a transient text panel at the bottom of the
    active window, and display the `msg` contents there.
    """

    def run(self, edit, msg=""):
        panel = self.view.window().create_output_panel(PANEL_NAME)
        panel.set_read_only(False)
        panel.erase(edit, sublime.Region(0, panel.size()))
        panel.insert(edit, 0, msg)
        panel.set_read_only(True)
        panel.show(0)
        self.view.window().run_command("show_panel", {"panel": "output.{}".format(PANEL_NAME)})
