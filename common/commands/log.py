import sublime
from sublime_plugin import TextCommand

PANEL_NAME = "GitSavvy"


class GsDisplayPanelCommand(TextCommand):

    """
    Given a `msg` string, open a transient text panel at the bottom of the
    active window, and display the `msg` contents there.
    """

    def run(self, edit, msg=""):
        panel_view = self.view.window().create_output_panel(PANEL_NAME)
        panel_view.set_read_only(False)
        panel_view.erase(edit, sublime.Region(0, panel_view.size()))
        panel_view.insert(edit, 0, msg)
        panel_view.set_read_only(True)
        panel_view.show(0)
        self.view.window().run_command("show_panel", {"panel": "output.{}".format(PANEL_NAME)})
