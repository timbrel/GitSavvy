import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from .log import LogMixin
from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel


NEW_BRANCH_PROMPT = "Branch name:"
NEW_BRANCH_INVALID = "`{}` is a invalid branch name.\nRead more on $(man git-check-ref-format)"


class GsCheckoutBranchCommand(WindowCommand, GitCommand):

    """
    Display a panel of all local branches.  Change to the branch the
    user selected.
    """

    def run(self, branch=None):
        sublime.set_timeout_async(lambda: self.run_async(branch), 0)

    def run_async(self, branch):
        if branch:
            self.on_branch_selection(branch)
        else:
            show_branch_panel(
                self.on_branch_selection,
                local_branches_only=True,
                ignore_current_branch=True)

    def on_branch_selection(self, branch):
        if not branch:
            return

        self.git("checkout", branch)
        self.window.status_message("Checked out `{}` branch.".format(branch))
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class GsCheckoutNewBranchCommand(WindowCommand, GitCommand):

    """
    Prompt the user for a new branch name, create it, and check it out.
    """

    def run(self, base_branch=None):
        sublime.set_timeout_async(lambda: self.run_async(base_branch))

    def run_async(self, base_branch=None, new_branch=None):
        self.base_branch = base_branch
        show_single_line_input_panel(
            NEW_BRANCH_PROMPT, new_branch or base_branch or "", self.on_done)

    def on_done(self, branch_name):
        if not self.validate_branch_name(branch_name):
            sublime.error_message(NEW_BRANCH_INVALID.format(branch_name))
            sublime.set_timeout_async(
                lambda: self.run_async(base_branch=self.base_branch, new_branch=branch_name), 100)
            return None

        self.git(
            "checkout", "-b",
            branch_name,
            self.base_branch if self.base_branch else None)
        self.window.status_message("Created and checked out `{}` branch.".format(branch_name))
        util.view.refresh_gitsavvy_interfaces(self.window,
                                              refresh_sidebar=True,
                                              interface_reset_cursor=True)


class GsCheckoutRemoteBranchCommand(WindowCommand, GitCommand):

    """
    Display a panel of all remote branches.  When the user makes a selection,
    create a corresponding local branch, and set it to the HEAD of the
    selected branch.
    """

    def run(self, remote_branch=None):
        sublime.set_timeout_async(lambda: self.run_async(remote_branch))

    def run_async(self, remote_branch):
        if remote_branch:
            self.on_branch_selection(remote_branch)
        else:
            show_branch_panel(
                self.on_branch_selection,
                remote_branches_only=True)

    def on_branch_selection(self, remote_branch, local_name=None):
        if not remote_branch:
            return

        self.remote_branch = remote_branch
        if not local_name:
            local_name = remote_branch.split("/", 1)[1]
        show_single_line_input_panel(
            NEW_BRANCH_PROMPT,
            local_name,
            self.on_enter_local_name)

    def on_enter_local_name(self, branch_name):
        if not self.validate_branch_name(branch_name):
            sublime.error_message(NEW_BRANCH_INVALID.format(branch_name))
            sublime.set_timeout_async(
                lambda: self.on_branch_selection(self.remote_branch, branch_name), 100)
            return None

        self.git("checkout", "-b", branch_name, "--track", self.remote_branch)
        self.window.status_message(
            "Checked out `{}` as local branch `{}`.".format(self.remote_branch, branch_name))
        util.view.refresh_gitsavvy_interfaces(self.window,
                                              refresh_sidebar=True,
                                              interface_reset_cursor=True)


class GsCheckoutCurrentFileAtCommitCommand(LogMixin, WindowCommand, GitCommand):

    """
    Reset the current active file to a given commit.
    """

    def run(self):
        if self.file_path:
            super().run(file_path=self.file_path)

    def on_highlight(self, commit):
        if not self.savvy_settings.get("log_show_more_commit_info", True):
            return
        if commit:
            self.window.run_command('gs_show_file_diff', {
                'commit_hash': commit,
                'file_path': self.file_path
            })

    @util.actions.destructive(description="discard uncommitted changes to file")
    def do_action(self, commit_hash, **kwargs):
        if commit_hash:
            self.checkout_ref(commit_hash, self.file_path)
            self.window.status_message(
                "Successfully checked out {} from {}.".format(
                    self.file_path,
                    self.get_short_hash(commit_hash)
                )
            )
            util.view.refresh_gitsavvy_interfaces(self.window, interface_reset_cursor=True)


class GsShowFileDiffCommand(WindowCommand, GitCommand):
    def run(self, commit_hash, file_path):
        self._commit_hash = commit_hash
        self._file_path = file_path
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        text = self.git(
            "diff",
            "--no-color",
            "-R",
            self._commit_hash,
            '--',
            self._file_path
        )

        output_view = self.window.create_output_panel("show_file_diff")
        output_view.set_read_only(False)
        output_view.run_command("gs_replace_view_text", {"text": text, "nuke_cursors": True})
        output_view.set_syntax_file("Packages/GitSavvy/syntax/diff.sublime-syntax")
        output_view.set_read_only(True)
        self.window.run_command("show_panel", {"panel": "output.show_file_diff"})
