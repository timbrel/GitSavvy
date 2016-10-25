import sublime
from . import GsLogCurrentBranchCommand
from ...common import util


class GsFixupFromStageCommand(GsLogCurrentBranchCommand):
    def run(self):
        (staged_entries,
         unstaged_entries,
         untracked_entries,
         conflict_entries) = self.sort_status_entries(self.get_status())

        if len(unstaged_entries) + len(untracked_entries) + len(conflict_entries) > 0:
            sublime.message_dialog(
                "Unable to perform rebase actions while repo is in unclean state."
            )
            return
        if len(staged_entries) == 0:
            sublime.message_dialog(
                "No staged files."
            )
            return
        super().run()

    def do_action(self, commit):
        commit = self.git("rev-parse", commit).strip()
        self.git("commit", "--fixup", commit)
        try:
            self.git("rebase", "-i", "-p", "--autosquash", "{}~1".format(commit),
                     custom_environ={"EDITOR": "cat"})
        except Exception as e:
            sublime.error_message("Error encountered. Cannot autosquash fixup.")
            self.git("rebase", "--abort")
            raise e
        finally:
            util.view.refresh_gitsavvy(self.window.active_view())


class GsQuickStageCurrentFileAndFixupCommand(GsFixupFromStageCommand):
    def run(self):
        self.git("add", "--", self.file_path)
        super().run()
