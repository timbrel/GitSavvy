import sublime
from sublime_plugin import WindowCommand

from .base_command import BaseCommand


NO_REMOTES_MESSAGE = "You have not configured any remotes."
START_PUSH_MESSAGE = "Starting push..."
END_PUSH_MESSAGE = "Push complete."


class GgPushCommand(WindowCommand, BaseCommand):

    def run(self):
        sublime.set_timeout_async(lambda: self.do_push())
        self.remotes = list(self.get_remotes().keys())

    def do_push(self):
        sublime.status_message(START_PUSH_MESSAGE)
        self.push(remote=None, branch=None)
        sublime.status_message(END_PUSH_MESSAGE)
        if self.window.active_view().settings().get("git_gadget.status_view"):
            self.window.active_view().run_command("gg_status_refresh")


class GgPushToBranchCommand(WindowCommand, BaseCommand):

    def run(self):
        self.remotes = list(self.get_remotes().keys())
        self.remote_branches = self.get_remote_branches()

        if not self.remotes:
            self.window.show_quick_panel([NO_REMOTES_MESSAGE], None)
        else:
            self.window.show_quick_panel(self.remotes, self.on_select_remote, sublime.MONOSPACE_FONT)

    def on_select_remote(self, remote_index):
        # If the user pressed `esc` or otherwise cancelled.
        if remote_index == -1:
            return

        self.selected_remote = self.remotes[remote_index]
        selected_remote_prefix = self.selected_remote + "/"

        self.branches_on_selected_remote = [
            branch for branch in self.remote_branches
            if branch.startswith(selected_remote_prefix)
        ]

        current_local_branch = self.get_current_branch_name()

        try:
            pre_selected_index = self.branches_on_selected_remote.index(
                selected_remote_prefix + current_local_branch)
        except ValueError:
            pre_selected_index = None

        def deferred_panel():
            self.window.show_quick_panel(
                self.branches_on_selected_remote,
                self.on_select_branch,
                sublime.MONOSPACE_FONT,
                pre_selected_index
            )

        sublime.set_timeout(deferred_panel)

    def on_select_branch(self, branch_index):
        # If the user pressed `esc` or otherwise cancelled.
        if branch_index == -1:
            return

        selected_branch = self.branches_on_selected_remote[branch_index].split("/", 1)[1]
        sublime.set_timeout_async(lambda: self.do_push(self.selected_remote, selected_branch))

    def do_push(self, remote, branch):
        sublime.status_message(START_PUSH_MESSAGE)
        self.push(remote, branch)
        sublime.status_message(END_PUSH_MESSAGE)
        if self.window.active_view().settings().get("git_gadget.status_view"):
            self.window.active_view().run_command("gg_status_refresh")
