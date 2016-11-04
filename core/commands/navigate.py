import sublime
from sublime_plugin import TextCommand

from ..git_command import GitCommand


class GsNavigate(TextCommand, GitCommand):

    """
    Move cursor to the next (or previous).
    """
    offset = 4

    def run(self, edit, forward=True):
        sel = self.view.sel()
        if not sel:
            return
        current_position = sel[0].a

        available_regions = self.get_available_regions()

        new_position = (self.forward(current_position, available_regions)
                        if forward
                        else self.backward(current_position, available_regions))

        if new_position is None:
            return

        sel.clear()
        # Position the cursor at the beginning of the file name.
        new_position += self.offset
        sel.add(sublime.Region(new_position, new_position))
        self.view.run_command("show_at_center")

    def forward(self, current_position, file_regions):
        for file_region in file_regions:
            if file_region.a > current_position:
                return file_region.a
        return None

    def backward(self, current_position, file_regions):
        for file_region in reversed(file_regions):
            if file_region.b < current_position:
                return file_region.a
        return None
