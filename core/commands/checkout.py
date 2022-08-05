from functools import partial

import sublime
from sublime_plugin import WindowCommand

from . import intra_line_colorizer
from .log import LogMixin
from ..git_command import GitCommand, GitSavvyError
from ..ui_mixins.quick_panel import show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel
from ..view import replace_view_content
from ...common import util
from GitSavvy.core import store
from GitSavvy.core.utils import noop, show_actions_panel


__all__ = (
    "gs_checkout_branch",
    "gs_checkout_new_branch",
    "gs_checkout_remote_branch",
    "gs_checkout_current_file_at_commit",
    "gs_show_file_diff",
)


NEW_BRANCH_PROMPT = "Branch name:"
NEW_BRANCH_INVALID = "`{}` is a invalid branch name.\nRead more on $(man git-check-ref-format)"


class gs_checkout_branch(WindowCommand, GitCommand):

    """
    Display a panel of all local branches.  Change to the branch the
    user selected.
    """

    def run(self, branch=None):
        if branch:
            self.on_branch_selection(branch)
        else:
            show_branch_panel(
                self.on_branch_selection,
                local_branches_only=True,
                ignore_current_branch=True,
                selected_branch=store.current_state(self.repo_path)["last_branches"][-2]
            )

    def on_branch_selection(self, branch, merge=False):
        try:
            self.git_throwing_silently(
                "checkout",
                "--merge" if merge else None,
                branch
            )
        except GitSavvyError as e:
            if (
                "Please commit your changes or stash them before you switch branches" in e.stderr
                and not merge
            ):
                show_actions_panel(self.window, [
                    noop("Abort, local changes would be overwritten by checkout."),
                    (
                        "Try a 'checkout --merge'.",
                        partial(self.on_branch_selection, branch, merge=True)
                    )
                ])
                return
            else:
                raise GitSavvyError(
                    e.message,
                    cmd=e.cmd,
                    stdout=e.stdout,
                    stderr=e.stderr,
                    show_panel=True,
                    window=e.window,
                )

        self.window.status_message("Checked out `{}`.".format(branch))
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_checkout_new_branch(WindowCommand, GitCommand):

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
        branch_name = branch_name.strip().replace(" ", "-")
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


class gs_checkout_remote_branch(WindowCommand, GitCommand):

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


class gs_checkout_current_file_at_commit(LogMixin, WindowCommand, GitCommand):

    """
    Reset the current active file to a given commit.
    """

    def run(self):
        if self.file_path:
            super().run(file_path=self.file_path)

    def on_highlight(self, commit, file_path=None):
        if not self.savvy_settings.get("log_show_more_commit_info", True):
            return
        if commit:
            self.window.run_command('gs_show_file_diff', {
                'commit_hash': commit,
                'file_path': file_path
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


class gs_show_file_diff(WindowCommand, GitCommand):
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

        view = self.window.create_output_panel("show_file_diff")
        view.set_syntax_file("Packages/GitSavvy/syntax/diff.sublime-syntax")
        view.set_read_only(True)
        replace_view_content(view, text)
        self.window.run_command("show_panel", {"panel": "output.show_file_diff"})
        intra_line_colorizer.annotate_intra_line_differences(view)
