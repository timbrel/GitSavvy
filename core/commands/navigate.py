import sublime
from sublime_plugin import TextCommand

from ..git_command import GitCommand
from GitSavvy.core.view import show_region


MYPY = False
if MYPY:
    from typing import Optional, Sequence


class GsNavigate(TextCommand, GitCommand):
    """
    Move cursor to the next (or previous).
    """

    offset = 4
    show_at_center = False
    wrap = True
    wrap_with_force = False
    _just_jumped = 0

    def run(self, edit, forward=True):
        self.forward = forward
        sel = self.view.sel()
        current_position = sel[0].a

        available_regions = self.get_available_regions()
        if not available_regions:
            return

        wanted_section = (
            self.next_region(current_position, available_regions)
            if forward
            else self.previous_region(current_position, available_regions)
        )
        if wanted_section is None:
            if self._just_jumped == 1:
                window = self.view.window()
                if window:
                    window.status_message("press again to wrap around ...")
            self._just_jumped -= 1
            return

        self._just_jumped = 2
        sel.clear()
        # Position the cursor at the beginning of the section...
        new_cursor_position = wanted_section.begin() + self.offset
        sel.add(sublime.Region(new_cursor_position))

        if self.show_at_center:
            self.view.show_at_center(new_cursor_position)
        else:
            # For the first entry, try to show the beginning of the buffer.
            # (Usually we have some info/help text there.)
            if wanted_section == available_regions[0]:
                wanted_section = sublime.Region(0, wanted_section.a)
                show_region(self.view, wanted_section, context=2, prefer_end=True)
            else:
                show_region(self.view, wanted_section, context=2)

    def get_available_regions(self):
        # type: () -> Sequence[sublime.Region]
        raise NotImplementedError()

    def next_region(self, current_position, regions):
        # type: (sublime.Point, Sequence[sublime.Region]) -> Optional[sublime.Region]
        for region in regions:
            if region.a > current_position:
                return region

        return regions[0] if self._wrap_around_now() else None

    def previous_region(self, current_position, regions):
        # type: (sublime.Point, Sequence[sublime.Region]) -> Optional[sublime.Region]
        for region in reversed(regions):
            if min(region.a + self.offset, region.b) < current_position:
                return region

        return regions[-1] if self._wrap_around_now() else None

    def _wrap_around_now(self):
        # type: () -> bool
        return self.wrap and (not self.wrap_with_force or self._just_jumped == 0)
