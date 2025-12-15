from collections import namedtuple
import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui__quick_panel import show_quick_panel


MenuOption = namedtuple("MenuOption", ["requires_action", "menu_text", "filename", "is_untracked"])


CLEAN_WORKING_DIR = "Nothing to commit, working directory clean."
ADD_ALL_UNSTAGED_FILES = " ?  All unstaged files"
ADD_ALL_FILES = " +  All files"
STAGED = "--- {} files are staged for commit. ---"
COMMIT = "    git: quick commit"
AMEND = "    git: amend from stage"
FIXUP = "    git: fixup from stage"


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
            selection = menu_options[id]

            if not selection.requires_action:
                return

            elif selection.menu_text == COMMIT:
                self.window.run_command("gs_quick_commit")
                return

            elif selection.menu_text == AMEND:
                self.window.run_command("gs_amend")
                return

            elif selection.menu_text == FIXUP:
                self.window.run_command("gs_fixup_from_stage")
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

            self.window.status_message("Successfully added `{}`.".format(
                scope_of_action))
            util.view.refresh_gitsavvy(self.window.active_view())

            sublime.set_timeout_async(self.run_async, 0)

        show_quick_panel(self.window, menu_entries, on_selection)

    def get_menu_options(self):
        """
        Determine the git status of the current working directory, and return
        a list of menu options for each file that is shown.
        """
        status = self.get_working_dir_status()
        if status.is_clean:
            return [MenuOption(False, CLEAN_WORKING_DIR, None, None)]

        menu_options = []

        for entry in status.unstaged_files:
            filename = (
                entry.path + " <- " + entry.path_alt
                if entry.path_alt
                else entry.path
            )
            menu_text = "[{0}] {1}".format(entry.working_status, filename)
            menu_options.append(MenuOption(True, menu_text, filename, False))

        for entry in status.untracked_files:
            menu_text = "[{0}] {1}".format(entry.working_status, entry.path)
            menu_options.append(MenuOption(True, menu_text, entry.path, True))

        for entry in status.merge_conflicts:
            menu_text = "[{0}] {1}".format(entry.working_status, entry.path)
            menu_options.append(MenuOption(True, menu_text, entry.path, False))

        if status.unstaged_files:
            menu_options.append(MenuOption(True, ADD_ALL_UNSTAGED_FILES, None, None))
            menu_options.append(MenuOption(True, ADD_ALL_FILES, None, None))

        staged_count = len(status.staged_files)
        if staged_count > 0:
            menu_options.append(MenuOption(False, STAGED.format(staged_count), None, None))
            menu_options.append(MenuOption(True, COMMIT, None, None))
            menu_options.append(MenuOption(True, AMEND, None, None))
            menu_options.append(MenuOption(True, FIXUP, None, None))

        return menu_options
