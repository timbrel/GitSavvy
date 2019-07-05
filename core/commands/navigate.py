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
        self.view.show_at_center(new_position)

        # The following shouldn't strictly be necessary, but Sublime sometimes
        # jumps to the right when show_at_center for a column-zero-point occurs.
        _, vp_y = self.view.viewport_position()
        self.view.set_viewport_position((0, vp_y), False)

    def forward(self, current_position, file_regions):
        for file_region in file_regions:
            if file_region.a > current_position:
                return file_region.a
        # If we are after the last match, pick the first one
        return file_regions[0].a if len(file_regions) != 0 else None

    def backward(self, current_position, file_regions):
        for file_region in reversed(file_regions):
            if file_region.b < current_position:
                return file_region.a
        # If we are after the last match, pick the last one
        return file_regions[-1].a if len(file_regions) != 0 else None
