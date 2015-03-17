import os
from itertools import groupby

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ...common import ui
from ..git_command import GitCommand
from ...common import util


class GsShowBranchCommand(WindowCommand, GitCommand):

    """
    Open a branch dashboard for the active Git repository.
    """

    def run(self):
        BranchInterface(view_attrs={"git_savvy.repo_path": self.repo_path})


class BranchInterface(ui.Interface, GitCommand):

    """
    """

    interface_type = "branch"
    read_only = True
    view_type = "branches"
    syntax_file = "Packages/GitSavvy/syntax/branch.tmLanguage"
    word_wrap = False

    dedent = 4
    skip_first_line = True

    template = """

      BRANCH:  {branch_status}
      ROOT:    {git_root}
      HEAD:    {head}

      LOCAL:
    {branch_list}{remotes}
      #############
      ## ACTIONS ##
      #############

      [c] checkout                                  [p] push selected to remote
      [b] create new branch (from HEAD)             [P] push all branches to remote
      [d] delete                                    [D] delete (force)
      [R] rename (local)                            [m] merge selected into active branch
      [t] configure tracking                        [M] fetch and merge into active branch

      [f] diff against active
      [r] refresh

    -
    """

    template_remote = """
      REMOTE ({remote_name}):
    {remote_branch_list}"""

    def title(self):
        return "BRANCHES: {}".format(os.path.basename(self.repo_path))

    def pre_render(self):
        self._branches = tuple(self.get_branches())

    @ui.partial("branch_status")
    def render_branch_status(self):
        return self.get_branch_status()

    @ui.partial("git_root")
    def render_git_root(self):
        return self.repo_path

    @ui.partial("head")
    def render_head(self):
        return self.get_latest_commit_msg_for_head()

    @ui.partial("branch_list")
    def render_branch_list(self, branches=None):
        if not branches:
            branches = [branch for branch in self._branches if not branch.remote]

        return "\n".join(
            "  {indicator} {hash:.7} {name} {tracking}".format(
                indicator="▸" if branch.active else " ",
                hash=branch.commit_hash,
                name=branch.name,
                tracking=("({branch}{status})".format(
                    branch=branch.tracking,
                    status=", " + branch.tracking_status if branch.tracking_status else ""
                    )
                    if branch.tracking else "")
                )
            for branch in branches
            )

    @ui.partial("remotes")
    def render_remotes(self):
        output_tmpl = "\n"
        render_fns = []

        for remote_name, branches in groupby(self._branches, lambda branch: branch.remote):
            if not remote_name:
                continue

            branches = tuple(branches)
            key = "branch_list_" + remote_name
            output_tmpl += "{" + key + "}\n"

            @ui.partial(key)
            def render(remote_name=remote_name, branches=branches):
                return self.template_remote.format(
                    remote_name=remote_name,
                    remote_branch_list=self.render_branch_list(branches=branches)
                    )

            render_fns.append(render)

        return output_tmpl, render_fns


ui.register_listeners(BranchInterface)


class GsBranchesCheckoutCommand(TextCommand, GitCommand):

    """
    Checkout the selected branch.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.interface = ui.get_interface(self.view.id())
        selection, line = self.interface.get_selection_line()
        if not line:
            return

        segments = line.strip("▸ ").split(" ")
        branch_name = segments[1]

        local_region = self.view.get_regions("git_savvy_interface.branch_list")[0]
        if local_region.contains(selection):
            self.checkout_ref(branch_name)
            util.view.refresh_gitsavvy(self.view)
            return

        remotes = self.get_remotes()
        for remote_name in remotes:
            remote_region = self.view.get_regions("git_savvy_interface.branch_list_" + remote_name)
            if remote_region and remote_region[0].contains(selection):
                self.checkout_ref("{}/{}".format(remote_name, branch_name))
                util.view.refresh_gitsavvy(self.view)
                return


class GsBranchesCreateNewCommand(TextCommand, GitCommand):

    """
    Create a new branch from HEAD and checkout.
    """

    def run(self, edit):
        self.view.window().run_command("gs_checkout_new_branch")


class GsBranchesDeleteCommand(TextCommand, GitCommand):

    """
    Delete selected branch.
    """

    def run(self, edit, force=False):
        self.force = force
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.interface = ui.get_interface(self.view.id())
        selection, line = self.interface.get_selection_line()
        if not line:
            return

        segments = line.strip("▸ ").split(" ")
        branch_name = segments[1]

        local_region = self.view.get_regions("git_savvy_interface.branch_list")[0]
        if local_region.contains(selection):
            self.delete_local_branch(branch_name)
            return

        remotes = self.get_remotes()
        for remote_name in remotes:
            remote_region = self.view.get_regions("git_savvy_interface.branch_list_" + remote_name)
            if remote_region and remote_region[0].contains(selection):
                self.delete_remote_branch(remote_name, branch_name)
                return

    @util.actions.destructive(description="delete a local branch")
    def delete_local_branch(self, branch_name):
        self.git(
            "branch",
            "-D" if self.force else "-d",
            branch_name
            )
        sublime.status_message("Deleted local branch.")
        util.view.refresh_gitsavvy(self.view)

    @util.actions.destructive(description="delete a remote branch")
    def delete_remote_branch(self, remote, branch_name):
        sublime.status_message("Deleting remote branch...")
        self.git(
            "push",
            "--force" if self.force else None,
            remote,
            ":"+branch_name
            )
        sublime.status_message("Deleted remote branch.")
        util.view.refresh_gitsavvy(self.view)


class GsBranchesRenameCommand(TextCommand, GitCommand):

    """
    Rename selected branch.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.interface = ui.get_interface(self.view.id())
        selection, line = self.interface.get_selection_line()
        if not line:
            return

        local_region = self.view.get_regions("git_savvy_interface.branch_list")[0]
        if not local_region.contains(selection):
            sublime.message_dialog("You can only delete local branches.")
            return

        segments = line.strip("▸ ").split(" ")
        self.old_name = segments[1]

        self.view.window().show_input_panel(
            "Enter new branch name (for {}):".format(self.old_name),
            self.old_name,
            self.on_entered_name,
            None,
            None
            )

    def on_entered_name(self, new_name):
        self.git("branch", "-m", self.old_name, new_name)
        util.view.refresh_gitsavvy(self.view)


class GsBranchesConfigureTrackingCommand(TextCommand, GitCommand):

    """
    Configure remote branch to track against for selected branch.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        """
        Display a panel of all remotes defined for the repo, then proceed to
        `on_select_remote`.  If no remotes are defined, notify the user and
        proceed no further.
        """
        self.interface = ui.get_interface(self.view.id())
        selection, line = self.interface.get_selection_line()
        if not line:
            return

        local_region = self.view.get_regions("git_savvy_interface.branch_list")[0]
        if not local_region.contains(selection):
            sublime.message_dialog("You can only setup tracking for local branches.")
            return

        segments = line.strip("▸ ").split(" ")
        self.local_branch = segments[1]

        self.remotes = list(self.get_remotes().keys())
        self.remote_branches = self.get_remote_branches()

        if not self.remotes:
            self.view.window().show_quick_panel(["There are no remotes available."], None)
        else:
            self.view.window().show_quick_panel(
                self.remotes,
                self.on_select_remote,
                flags=sublime.MONOSPACE_FONT
                )

    def on_select_remote(self, remote_index):
        """
        After the user selects a remote, display a panel of branches that are
        present on that remote, then proceed to `on_select_branch`.
        """
        # If the user pressed `esc` or otherwise cancelled.
        if remote_index == -1:
            return

        self.selected_remote = self.remotes[remote_index]
        selected_remote_prefix = self.selected_remote + "/"

        self.branches_on_selected_remote = [
            branch for branch in self.remote_branches
            if branch.startswith(selected_remote_prefix)
        ]

        try:
            pre_selected_index = self.branches_on_selected_remote.index(
                selected_remote_prefix + self.local_branch)
        except ValueError:
            pre_selected_index = 0

        self.view.window().show_quick_panel(
            self.branches_on_selected_remote,
            self.on_select_branch,
            flags=sublime.MONOSPACE_FONT,
            selected_index=pre_selected_index
        )

    def on_select_branch(self, branch_index):
        """
        Determine the actual branch name of the user's selection, and proceed
        to `do_pull`.
        """
        # If the user pressed `esc` or otherwise cancelled.
        if branch_index == -1:
            return
        selected_remote_branch = self.branches_on_selected_remote[branch_index].split("/", 1)[1]
        remote_ref = self.selected_remote + "/" + selected_remote_branch


        self.git("branch", "-u", remote_ref, self.local_branch)
        util.view.refresh_gitsavvy(self.view)


class GsBranchesPushSelectedCommand(TextCommand, GitCommand):

    """
    Push selected branch to remote.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        self.interface = ui.get_interface(self.view.id())
        selection, line = self.interface.get_selection_line()
        if not line:
            return

        local_region = self.view.get_regions("git_savvy_interface.branch_list")[0]
        if not local_region.contains(selection):
            sublime.message_dialog("You can only delete local branches.")
            return

        segments = line.strip("▸ ").split(" ")
        self.branch_name = segments[1]

        self.remotes = list(self.get_remotes().keys())

        if not self.remotes:
            self.view.window().show_quick_panel(["There are no remotes available."], None)
        else:
            self.view.window().show_quick_panel(
                self.remotes,
                self.on_select_remote,
                flags=sublime.MONOSPACE_FONT
                )

    def on_select_remote(self, remote_index):
        # If the user pressed `esc` or otherwise cancelled.
        if remote_index == -1:
            return
        selected_remote = self.remotes[remote_index]
        self.push(remote=selected_remote, branch=self.branch_name)
        util.view.refresh_gitsavvy(self.view)


class GsBranchesPushAllCommand(TextCommand, GitCommand):

    """
    Push all branches to remote.
    """

    def run(self, edit):
        pass


class GsBranchesMergeSelectedCommand(TextCommand, GitCommand):

    """
    Merge selected branch into active branch.
    """

    def run(self, edit):
        pass


class GsBranchesFetchAndMergeCommand(TextCommand, GitCommand):

    """
    Fetch from remote and merge fetched branch into active branch.
    """

    def run(self, edit):
        pass


class GsBranchesDiffBranchCommand(TextCommand, GitCommand):

    """
    Show a diff comparing the selected branch to the active branch.
    """

    def run(self, edit):
        pass


class GsBranchesRefreshCommand(TextCommand, GitCommand):

    """
    Refresh the branch dashboard.
    """

    def run(self, edit):
        pass
