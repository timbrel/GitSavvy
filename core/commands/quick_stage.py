from collections import namedtuple
import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util


MenuOption = namedtuple("MenuOption", ["requires_action", "menu_text", "filename", "is_untracked"])


CLEAN_WORKING_DIR = "Nothing to commit, working directory clean."
ADD_ALL_UNSTAGED_FILES = " ?  All unstaged files"
ADD_ALL_FILES = " +  All files"


class GsQuickStageCommand(WindowCommand, GitCommand):

    """
    Display a quick panel of unstaged files in the current git repository,
    allowing the user to select one or more files for staging.

    Display filenames with one of the following indicators:

        * [M] modified
        * [A] added
        * [D] deleted
        * [R] renamed/moved
        * [C] copied
        * [U] updated but unmerged
        * [?] untracked

    """

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        menu_options = self.get_menu_options()
        menu_entries = [f.menu_text for f in menu_options]

        def on_selection(id):
            if id == -1:
                return

            selection = menu_options[id]

            if not selection.requires_action:
                return

            elif selection.menu_text == ADD_ALL_UNSTAGED_FILES:
                self.git("add", "--update", ".")
                scope_of_action = "all unstaged files"

            elif selection.menu_text == ADD_ALL_FILES:
                self.git("add", "--all")
                scope_of_action = "all files"

            elif selection.is_untracked:
                self.git("add", "--", selection.filename)
                scope_of_action = "`{}`".format(selection.filename)

            else:
                self.git("add", "--update", "--", selection.filename)
                scope_of_action = "`{}`".format(selection.filename)

            sublime.status_message("Successfully added `{}`.".format(
                scope_of_action))
            util.view.refresh_gitsavvy(self.window.active_view())

            sublime.set_timeout_async(self.run_async, 0)

        self.window.show_quick_panel(
            menu_entries,
            on_selection,
            flags=sublime.MONOSPACE_FONT
            )

    def get_menu_options(self):
        """
        Determine the git status of the current working directory, and return
        a list of menu options for each file that is shown.
        """
        status_entries = self.get_status()
        menu_options = []

        for entry in status_entries:
            if entry.working_status in ("M", "D", "?"):
                filename = (entry.path if not entry.index_status == "R"
                            else entry.path + " <- " + entry.path_alt)
                menu_text = "[{0}] {1}".format(entry.working_status, filename)
                menu_options.append(MenuOption(True, menu_text, filename, entry.index_status == "?"))

        if not menu_options:
            return [MenuOption(False, CLEAN_WORKING_DIR, None, None)]

        menu_options.append(MenuOption(True, ADD_ALL_UNSTAGED_FILES, None, None))
        menu_options.append(MenuOption(True, ADD_ALL_FILES, None, None))

        return menu_options
