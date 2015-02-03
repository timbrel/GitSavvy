import sublime
from sublime_plugin import TextCommand


PANEL_NAME = "GitGadget"


def panel(*msgs):
    msg = "\n".join(msgs)
    sublime.active_window().active_view().run_command("gg_display_panel", {"msg": msg})


class GgDisplayPanelCommand(TextCommand):

    def run(self, edit, msg=""):
        panel = self.view.window().create_output_panel(PANEL_NAME)
        panel.set_read_only(False)
        panel.erase(edit, sublime.Region(0, panel.size()))
        panel.insert(edit, 0, msg)
        panel.set_read_only(True)
        panel.show(0)
        self.view.window().run_command("show_panel", {"panel": "output.{}".format(PANEL_NAME)})
