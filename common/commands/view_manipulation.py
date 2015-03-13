import sublime
from sublime_plugin import TextCommand


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

    def run(self, edit, text, nuke_cursors=False):
        cursors_num = len(self.view.sel())
        is_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), text)
        self.view.set_read_only(is_read_only)

        if not cursors_num or nuke_cursors:
            selections = self.view.sel()
            selections.clear()
            pt = sublime.Region(0, 0)
            selections.add(pt)


class GsReplaceRegionCommand(TextCommand):

    """
    Replace the contents of a region within the view with the provided text.
    """

    def run(self, edit, text, begin, end):
        is_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(begin, end), text)
        self.view.set_read_only(is_read_only)
