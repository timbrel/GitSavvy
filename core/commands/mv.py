import functools
import os
import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ..ui_mixins.input_panel import show_single_line_input_panel


class GsMvCurrentFileCommand(WindowCommand, GitCommand):

    """
    Prompt the user for a new name for the current file.
    """

    def run(self):
        if self.file_path:
            parent, base_name = os.path.split(self.file_path)
            on_done = functools.partial(
                self.on_done,
                self.file_path, parent, base_name)
            v = show_single_line_input_panel(
                "New Name:", base_name,
                on_done, None, None)
            name, ext = os.path.splitext(base_name)
            v.sel().clear()
            v.sel().add(sublime.Region(0, len(name)))

    def on_done(self, file_path, parent, base_name, new_name):
        if new_name == base_name:
            return
        new_path = os.path.join(parent, new_name)

        self.git("mv", file_path, new_path)
        v = self.window.find_open_file(file_path)
        if v:
            v.retarget(new_path)
