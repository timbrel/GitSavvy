import sublime
from sublime_plugin import TextCommand

from ..git_command import GitCommand
from GitSavvy.core.view import show_region


MYPY = False
if MYPY:
    from typing import Dict, Optional, Sequence


class GsNavigate(TextCommand, GitCommand):
    """
    Move cursor to the next (or previous).
    """

    offset = 4
    show_at_center = True
    wrap = True
    _cache = {}  # type: Dict[int, Sequence[sublime.Region]]

    def run(self, edit, forward=True):
        sel = self.view.sel()
        current_position = sel[0].a

        available_regions = self._get_available_regions()
        if not available_regions:
            return
        wanted_section = (
            self.forward(current_position, available_regions)
            if forward
            else self.backward(current_position, available_regions)
        )
        if not wanted_section:
            return

        sel.clear()
        # Position the cursor at the beginning of the section...
        new_cursor_position = wanted_section.begin() + self.offset
        sel.add(sublime.Region(new_cursor_position))

        if self.show_at_center:
            self.view.show_at_center(new_cursor_position)
        else:
            show_region(self.view, wanted_section)

    def _get_available_regions(self):
        # type: () -> Sequence[sublime.Region]
        id = self.view.change_count()
        if id in self._cache:
            available_regions = self._cache[id]
        else:
            available_regions = self.get_available_regions()
            self._cache = {id: available_regions}
        return available_regions

    def get_available_regions(self):
        # type: () -> Sequence[sublime.Region]
        raise NotImplementedError()

    def forward(self, current_position, regions):
        # type: (sublime.Point, Sequence[sublime.Region]) -> Optional[sublime.Region]
        for region in regions:
            if region.a > current_position:
                return region

        return regions[0] if self.wrap else None

    def backward(self, current_position, regions):
        # type: (sublime.Point, Sequence[sublime.Region]) -> Optional[sublime.Region]
        for region in reversed(regions):
            if region.b < current_position:
                return region

        return regions[-1] if self.wrap else None
