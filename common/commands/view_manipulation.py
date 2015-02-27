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
