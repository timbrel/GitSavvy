import sublime
import re
from .log import gs_log_current_branch
from ...common import util

fixup_command = re.compile("^fixup! (.*)")


class GsFixupFromStageCommand(gs_log_current_branch):
    def run(self):  # type: ignore[override]
        status = self.get_working_dir_status()
        if status.unstaged_files or status.merge_conflicts:
            sublime.message_dialog(
                "Unable to perform rebase actions while repo is in unclean state."
            )
            return
        if not status.staged_files:
            sublime.message_dialog("No staged files.")
            return
        super().run()

    def auto_squash(self, commit_chain):
        fixup_idx = len(commit_chain) - 1
        msg = commit_chain[fixup_idx].msg
        m = fixup_command.match(msg)
        if m:
            orig_msg = m.group(1)
            orig_commit_indx = fixup_idx - 1
            while orig_commit_indx >= 0:
                if commit_chain[orig_commit_indx].msg.startswith(orig_msg):
                    break
                orig_commit_indx = orig_commit_indx - 1

            if orig_commit_indx >= 0:
                commit_chain.insert(orig_commit_indx + 1, commit_chain.pop(fixup_idx))
                original_commit = commit_chain[orig_commit_indx]
                next_commit = commit_chain[orig_commit_indx + 1]
                original_commit.do_commit = False
                next_commit.msg = original_commit.msg
                next_commit.datetime = original_commit.datetime
                next_commit.author = original_commit.author
                next_commit.modified = True

        return commit_chain

    def do_action(self, commit_hash, **kwargs):
        commit = self.git("rev-parse", commit_hash).strip()
        self.git("commit", "--fixup", commit)
        try:
            base_commit = self.git("rev-parse", "{}~1".format(commit)).strip()
            entries = self.log_rebase(base_commit, preserve=True)
            commit_chain = self.auto_squash(self.perpare_rewrites(entries))

            self.rewrite_active_branch(base_commit, commit_chain)
        except Exception as e:
            self.git("reset", "--soft", "HEAD^")
            sublime.message_dialog("Error encountered. Cannot autosquash fixup.")
            raise e
        finally:
            util.view.refresh_gitsavvy(self.window.active_view())


class GsQuickStageCurrentFileAndFixupCommand(GsFixupFromStageCommand):
    def run(self):  # type: ignore[override]
        self.git("add", "--", self.file_path)
        super().run()
