import sublime
from sublime_plugin import TextCommand


class GsRebaseInteractiveTerminalCommand(TextCommand):
    """ Change current lines to be change to new type """

    def run(self, edit, type):
        scopes = self.view.find_by_selector('meta.git-savvy.rebase-interactive.line')
        for sel in self.view.sel():
            line = self.view.line(sel.a)
            first_space = self.view.substr(line).find(" ")
            region = sublime.Region(line.a, line.a + first_space)
            self.view.replace(edit, region, type)
