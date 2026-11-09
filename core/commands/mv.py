import functools
import os
import sublime


from GitSavvy.core.base_commands import GsWindowCommand
from ..ui_mixins.input_panel import show_single_line_input_panel


__all__ = (
    "gs_mv_current_file",
)


class gs_mv_current_file(GsWindowCommand):

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
