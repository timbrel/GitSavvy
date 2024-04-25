import sublime
from sublime_plugin import TextCommand

from ..git_command import GitCommand
from ..utils import flash
from GitSavvy.core.view import show_region


from typing import Optional, Sequence


class GsNavigate(TextCommand, GitCommand):
    """
    Move cursor to the next (or previous).
    """

    offset = 4
    show_at_center = False
    wrap = True
    wrap_with_force = False
    shrink_to_cursor = True
    log_position = False
    # For the first entry, try to show the beginning of the buffer.
    # (Usually we have some info/help text there.)
    first_region_may_expand_to_bof = True

    _just_jumped = 0

    def run(self, edit, forward=True):
        self.forward = forward
        sel = self.view.sel()
        current_position = sel[0].b

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
                flash(self.view, "press again to wrap around ...")
            self._just_jumped -= 1
            return

        if self.log_position:
            idx = available_regions.index(wanted_section)
            flash(self.view, f"[{idx + 1}/{len(available_regions)}]")

        self._just_jumped = 2
        sel.clear()
        if self.shrink_to_cursor or self.offset:
            new_cursor_position = sublime.Region(wanted_section.begin() + self.offset)
        else:
            new_cursor_position = sublime.Region(wanted_section.b, wanted_section.a)
        sel.add(new_cursor_position)

        if self.show_at_center:
            self.view.show_at_center(new_cursor_position)
        else:
            if self.first_region_may_expand_to_bof and wanted_section == available_regions[0]:
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
