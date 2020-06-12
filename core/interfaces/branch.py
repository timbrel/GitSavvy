from itertools import groupby
import os

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ...common import ui, util
from ..commands import GsNavigate
from ..commands.log import LogMixin
from ..git_command import GitCommand
from ..ui_mixins.quick_panel import show_remote_panel, show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel


class GsShowBranchCommand(WindowCommand, GitCommand):

    """
    Open a branch dashboard for the active Git repository.
    """

    def run(self):
        BranchInterface(repo_path=self.repo_path)


class BranchInterface(ui.Interface, GitCommand):

    """
    Branch dashboard.
    """

    interface_type = "branch"
    read_only = True
    syntax_file = "Packages/GitSavvy/syntax/branch.sublime-syntax"
    word_wrap = False
    tab_size = 2

    show_remotes = None

    template = """\

      BRANCH:  {branch_status}
      ROOT:    {git_root}
      HEAD:    {head}

      LOCAL:
    {branch_list}{remotes}
    {< help}
    """

    template_help = """
      #############
      ## ACTIONS ##
      #############

      [c] checkout                                  [p] push selected to remote
      [b] create from selected branch               [P] push all branches to remote
      [d] delete                                    [h] fetch remote branches
      [D] delete (force)                            [m] merge selected into active branch
      [R] rename (local)                            [M] fetch and merge into active branch
      [t] configure tracking

      [f] diff against active                       [l] show branch log
      [H] diff history against active               [g] show branch log graph
      [E] edit branch description

      [e]         toggle display of remote branches
      [tab]       transition to next dashboard
      [SHIFT-tab] transition to previous dashboard
      [r]         refresh
      [?]         toggle this help menu

    -
    """

    template_remote = """
      REMOTE ({remote_name}):
    {remote_branch_list}"""

    def title(self):
        return "BRANCHES: {}".format(os.path.basename(self.repo_path))

    def pre_render(self):
        sort_by_recent = self.savvy_settings.get("sort_by_recent_in_branch_dashboard")
        self._branches = tuple(self.get_branches(sort_by_recent, fetch_descriptions=True))

    def on_new_dashboard(self):
        self.view.run_command("gs_branches_set_cursor")

    def reset_cursor(self):
        self.view.run_command("gs_branches_set_cursor")

    @ui.partial("branch_status")
    def render_branch_status(self):
        return self.get_branch_status(delim="\n           ")

    @ui.partial("git_root")
    def render_git_root(self):
        return self.short_repo_path

    @ui.partial("head")
    def render_head(self):
        return self.get_latest_commit_msg_for_head()

    @ui.partial("branch_list")
    def render_branch_list(self, branches=None):
        if not branches:
            branches = [branch for branch in self._branches if not branch.remote]

        return "\n".join(
            "  {indicator} {hash:.7} {name}{tracking}{description}".format(
                indicator="▸" if branch.active else " ",
                hash=branch.commit_hash,
                name=branch.name,
                description=" " + branch.description if branch.description else "",
                tracking=(" ({branch}{status})".format(
                    branch=branch.tracking,
                    status=", " + branch.tracking_status if branch.tracking_status else ""
                ) if branch.tracking else "")
            ) for branch in branches
        )

    @ui.partial("remotes")
    def render_remotes(self):
        if self.show_remotes is None:
            self.show_remotes = self.savvy_settings.get("show_remotes_in_branch_dashboard")

        return (self.render_remotes_on()
                if self.show_remotes else
                self.render_remotes_off())

    @ui.partial("help")
    def render_help(self):
        help_hidden = self.view.settings().get("git_savvy.help_hidden")
        if help_hidden:
            return ""
        else:
            return self.template_help

    def render_remotes_off(self):
        return "\n\n  ** Press [e] to toggle display of remote branches. **\n"

    def render_remotes_on(self):
        output_tmpl = "\n"
        render_fns = []

        sorted_branches = sorted(
            [b for b in self._branches if b.remote], key=lambda branch: branch.remote)
        for remote_name, branches in groupby(sorted_branches, lambda branch: branch.remote):
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

    def get_selected_branch(self):
        """
        Get a single selected branch. If more then one branch are selected, return (None, None).
        """
        selected_branches = self.get_selected_branches()
        if selected_branches and len(selected_branches) == 1:
            return selected_branches[0]
        else:
            return (None, None)

    def get_selected_branches(self, ignore_current_branch=False):
        current_branch_name = self.get_current_branch_name()
        branches = set()
        for sel in self.view.sel():
            for line in util.view.get_lines_from_regions(self.view, [sel]):
                branch = self._get_selected_branch_name(sel, line)
                if branch:
                    if ignore_current_branch and \
                            (branch[0] is None and branch[1] == current_branch_name):
                        continue
                    branches.add(branch)

        return list(branches)

    def _get_selected_branch_name(self, selection, line):
        segments = line.strip("▸ ").split(" ")
        if len(segments) <= 1:
            return None
        branch_name = segments[1]
        local_region = self.view.get_regions("git_savvy_interface.branch_list")[0]
        if local_region.contains(selection):
            return (None, branch_name)

        remotes = self.get_remotes()
        for remote_name in remotes:
            remote_region = self.view.get_regions("git_savvy_interface.branch_list_" + remote_name)
            if remote_region and remote_region[0].contains(selection):
                return (remote_name, branch_name)

    def create_branches_strs(self, branches):
        branches_strings = set()
        for branch in branches:
            if branch[0] is None:
                branches_strings.add(branch[1])
            else:
                branches_strings.add("{}/{}".format(branch[0], branch[1]))
        return branches_strings


ui.register_listeners(BranchInterface)


class GsBranchesCheckoutCommand(TextCommand, GitCommand):

    """
    Checkout the selected branch.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        remote_name, branch_name = interface.get_selected_branch()
        if not branch_name:
            return

        if remote_name:
            ref = "{}/{}".format(remote_name, branch_name)
        else:
            ref = branch_name
        self.view.window().run_command("gs_checkout_branch", {"branch": ref})


class GsBranchesCreateNewCommand(TextCommand, GitCommand):

    """
    Create a new branch from selected branch and checkout.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        remote_name, branch_name = interface.get_selected_branch()
        if not branch_name:
            return

        if remote_name:
            ref = "{}/{}".format(remote_name, branch_name)
            self.view.window().run_command("gs_checkout_remote_branch", {"remote_branch": ref})
        else:
            ref = branch_name
            self.view.window().run_command("gs_checkout_new_branch", {"base_branch": ref})


class GsBranchesDeleteCommand(TextCommand, GitCommand):

    """
    Delete selected branch.
    """

    def run(self, edit, force=False):
        self.force = force
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        window = self.view.window()
        if not window:
            return

        interface = ui.get_interface(self.view.id())
        remote_name, branch_name = interface.get_selected_branch()
        if not branch_name:
            return

        if remote_name:
            self.delete_remote_branch(remote_name, branch_name, window)
        else:
            window.run_command("gs_delete_branch", {"branch": branch_name, "force": self.force})

    @util.actions.destructive(description="delete a remote branch")
    def delete_remote_branch(self, remote, branch_name, window):
        window.status_message("Deleting remote branch...")
        self.git(
            "push",
            "--force" if self.force else None,
            remote,
            ":" + branch_name
        )
        window.status_message("Deleted remote branch.")
        util.view.refresh_gitsavvy(self.view)


class GsBranchesRenameCommand(TextCommand, GitCommand):

    """
    Rename selected branch.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        remote_name, branch_name = interface.get_selected_branch()
        if not branch_name or remote_name:
            return
        self.branch_name = branch_name

        show_single_line_input_panel(
            "Enter new branch name (for {}):".format(self.branch_name),
            self.branch_name,
            self.on_entered_name
        )

    def on_entered_name(self, new_name):
        self.git("branch", "-m", self.branch_name, new_name)
        util.view.refresh_gitsavvy(self.view)


class GsBranchesConfigureTrackingCommand(TextCommand, GitCommand):

    """
    Configure remote branch to track against for selected branch.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        remote_name, branch_name = interface.get_selected_branch()
        if not branch_name or remote_name:
            return

        self.local_branch = branch_name

        show_branch_panel(
            self.on_branch_selection,
            ask_remote_first=True,
            selected_branch=branch_name)

    def on_branch_selection(self, branch):
        # If the user pressed `esc` or otherwise cancelled.
        if not branch:
            return

        self.git("branch", "-u", branch, self.local_branch)
        util.view.refresh_gitsavvy(self.view)


class GsBranchesPushSelectedCommand(TextCommand, GitCommand):

    """
    Push selected branch to remote.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        remote_name, branch_name = interface.get_selected_branch()

        if not branch_name or remote_name:
            return

        self.view.window().run_command("gs_push", {"local_branch_name": branch_name})


class GsBranchesPushAllCommand(TextCommand, GitCommand):

    """
    Push all branches to remote.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        show_remote_panel(self.on_remote_selection)

    def on_remote_selection(self, remote):
        # If the user pressed `esc` or otherwise cancelled.
        if not remote:
            return
        self.view.window().status_message("Pushing all branches to `{}`...".format(remote))
        self.git("push", remote, "--all")
        self.view.window().status_message("Push successful.")
        util.view.refresh_gitsavvy(self.view)


class GsBranchesMergeSelectedCommand(TextCommand, GitCommand):

    """
    Merge selected branch into active branch.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.interface = ui.get_interface(self.view.id())

        branches = self.interface.get_selected_branches(ignore_current_branch=True)
        branches_strings = self.interface.create_branches_strs(branches)
        try:
            self.merge(branches_strings)
        finally:
            util.view.refresh_gitsavvy(self.view)


class GsBranchesFetchAndMergeCommand(TextCommand, GitCommand):

    """
    Fetch from remote and merge fetched branch into active branch.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.interface = ui.get_interface(self.view.id())

        branches = self.interface.get_selected_branches(ignore_current_branch=True)

        for branch in branches:
            if branch[0] is None:
                # update local branches which have tracking remote
                local_branch = self.get_local_branch(branch[1])
                if local_branch.tracking:
                    remote, remote_branch = local_branch.tracking.split("/", 1)
                    self.fetch(remote=remote, branch=branch[1], remote_branch=remote_branch)
            else:
                # fetch remote branches
                self.fetch(remote=branch[0], branch=branch[1])

        branches_strings = self.interface.create_branches_strs(branches)
        try:
            self.merge(branches_strings)
            self.view.window().status_message("Fetch and merge complete.")
        finally:
            util.view.refresh_gitsavvy(self.view)


class GsBranchesDiffBranchCommand(TextCommand, GitCommand):

    """
    Show a diff comparing the selected branch to the active branch.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        remote_name, branch_name = interface.get_selected_branch()
        if not branch_name:
            return
        self.show_diff(branch_name, remote=remote_name)

    def show_diff(self, branch_name, remote=None):
        comparison_branch_name = remote + "/" + branch_name if remote else branch_name
        active_branch_name = self.get_current_branch_name()
        self.view.window().run_command("gs_diff", {
            "base_commit": comparison_branch_name,
            "target_commit": active_branch_name,
            "disable_stage": True,
            "title": "DIFF: {}..{}".format(comparison_branch_name, active_branch_name)
        })


class GsBranchesDiffCommitHistoryCommand(TextCommand, GitCommand):

    """
    Show a view of all commits diff between branches.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        remote_name, branch_name = interface.get_selected_branch()
        if not branch_name:
            return
        self.show_commits(branch_name, remote=remote_name)

    def show_commits(self, branch_name, remote=None):
        target_commit = self.get_current_branch_name()
        base_commit = remote + "/" + branch_name if remote else branch_name
        self.view.window().run_command("gs_compare_commit", {
            "base_commit": base_commit,
            "target_commit": target_commit
        })


class GsBranchesRefreshCommand(TextCommand, GitCommand):

    """
    Refresh the branch dashboard.
    """

    def run(self, edit):
        util.view.refresh_gitsavvy(self.view)


class GsBranchesToggleRemotesCommand(TextCommand, GitCommand):

    """
    Toggle display of the remote branches.
    """

    def run(self, edit, show=None):
        interface = ui.get_interface(self.view.id())
        if show is None:
            interface.show_remotes = not interface.show_remotes
        else:
            interface.show_remotes = show
        interface.render()


class GsBranchesFetchCommand(TextCommand, GitCommand):

    """
    Prompt for remote and fetch branches.
    """

    def run(self, edit):
        self.view.window().run_command("gs_fetch")


class GsBranchesEditBranchDescriptionCommand(TextCommand, GitCommand):

    """
    Save a description for the selected branch
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        interface = ui.get_interface(self.view.id())
        remote_name, branch_name = interface.get_selected_branch()
        if not branch_name or remote_name:
            return

        self.branch_name = branch_name

        current_description = self.git(
            "config",
            "branch.{}.description".format(self.branch_name),
            throw_on_stderr=False
        ).strip(" \n")

        show_single_line_input_panel(
            "Enter new description (for {}):".format(self.branch_name),
            current_description,
            self.on_entered_description
        )

    def on_entered_description(self, new_description):
        unset = None if new_description else "--unset"

        self.git(
            "config",
            unset,
            "branch.{}.description".format(self.branch_name),
            new_description.strip("\n")
        )
        util.view.refresh_gitsavvy(self.view)


class GsBranchesNavigateBranchCommand(GsNavigate):

    """
    Move cursor to the next (or previous) selectable branch in the dashboard.
    """

    def get_available_regions(self):
        return [
            branch_region
            for region in self.view.find_by_selector(
                "meta.git-savvy.branches.branch"
            )
            for branch_region in self.view.lines(region)]


class GsBranchesSetCursorCommand(GsNavigate):

    """
    Move cursor to the active branch.
    """

    def get_available_regions(self):
        return [
            branch_region
            for region in self.view.find_by_selector(
                "meta.git-savvy.branches.branch string.other.git-savvy.branches.active-branch.sha1"
            )
            for branch_region in self.view.lines(region)]


class GsBranchesLogCommand(LogMixin, TextCommand, GitCommand):

    """
    Show log for the selected branch.
    """

    def run_async(self, **kwargs):
        interface = ui.get_interface(self.view.id())
        remote_name, branch_name = interface.get_selected_branch()
        if not branch_name:
            return

        # prefix the (optional) remote name to branch
        if remote_name:
            branch = '{remote}/{branch}'.format(
                remote=remote_name, branch=branch_name)
        else:
            branch = branch_name
        super().run_async(branch=branch)


class GsBranchesLogGraphCommand(WindowCommand, GitCommand):

    """
    Show log graph for the selected branch.
    """

    def run(self):
        view = self.window.active_view()
        interface = ui.get_interface(view.id())
        remote_name, branch_name = interface.get_selected_branch()
        if not branch_name:
            return

        # prefix the (optional) remote name to branch
        if remote_name:
            branch = '{remote}/{branch}'.format(
                remote=remote_name, branch=branch_name
            )
        else:
            branch = branch_name

        self.window.run_command('gs_graph', {
            'all': True,
            'branches': [branch],
            'follow': branch
        })
