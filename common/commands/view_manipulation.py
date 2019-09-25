import sublime
from sublime_plugin import TextCommand
from ...core.settings import GitSavvySettings


class GsInsertTextAtCursorCommand(TextCommand):

    """
    Insert the provided text at the current cursor position(s).
    """

    def run(self, edit, text):
        text_len = len(text)
        selected_ranges = []

        for region in self.view.sel():
            selected_ranges.append((region.begin(), region.end()))
            self.view.replace(edit, region, text)

        self.view.sel().clear()
        self.view.sel().add_all([sublime.Region(begin + text_len, end + text_len)
                                 for begin, end in selected_ranges])


class GsReplaceViewTextCommand(TextCommand):

    """
    Replace the contents of the view with the provided text and optional callback.
    If cursors exist, make sure to place them where they were.  Otherwise, add
    a single cursor at the start of the file.
    """

    def run(self, edit, text, nuke_cursors=False, restore_cursors=False):
        cursors_num = len(self.view.sel())
        if restore_cursors:
            save_cursors = [self.view.rowcol(s.a) for s in self.view.sel()]

        # Always clear the selection before replacing the view. Otherwise you
        # will have a flash where all the text is selected.
        self.view.sel().clear()

        is_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), text)
        self.view.set_read_only(is_read_only)

        if cursors_num == 0 or nuke_cursors:
            self.view.sel().clear()
            pt = sublime.Region(0, 0)
            self.view.sel().add(pt)

        elif restore_cursors:
            self.view.sel().clear()
            for (row, col) in save_cursors:
                cursor = self.view.text_point(row, col)
                self.view.sel().add(cursor)


class GsReplaceRegionCommand(TextCommand):

    """
    Replace the contents of a region within the view with the provided text.
    """

    def run(self, edit, text, begin, end):
        is_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(begin, end), text)
        self.view.set_read_only(is_read_only)


class GsHandleVintageousCommand(TextCommand):

    """
    Set the vintageous_friendly view setting if needed.
    Enter insert mode if vintageous_enter_insert_mode option is enabled.
    """

    def run(self, edit):
        savvy_settings = GitSavvySettings()
        if savvy_settings.get("vintageous_friendly", False) is True:
            self.view.settings().set("git_savvy.vintageous_friendly", True)
            if savvy_settings.get("vintageous_enter_insert_mode", False) is True:
                self.view.settings().set("vintageous_reset_mode_when_switching_tabs", False)
                self.view.run_command("_enter_insert_mode")


class GsHandleArrowKeysCommand(TextCommand):

    """
    Set the arrow_keys_navigation view setting if needed.
    It allows navigation by using arrow keys.
    """

    def run(self, edit):
        savvy_settings = GitSavvySettings()
        if savvy_settings.get("arrow_keys_navigation", False) is True:
            self.view.settings().set("git_savvy.arrow_keys_navigation", True)
