from contextlib import contextmanager
from functools import partial
import os

from sublime_plugin import WindowCommand

from ...common import ui, util
from ..commands import GsNavigate
from ..commands.log import LogMixin
from ..git_command import GitCommand
from ..ui_mixins.quick_panel import show_remote_panel, show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel
from GitSavvy.core.fns import filter_
from GitSavvy.core.utils import flash
from GitSavvy.core.runtime import enqueue_on_worker, on_new_thread, on_worker


__all__ = (
    "gs_show_branch",
    "gs_branches_checkout",
    "gs_branches_create_new",
    "gs_branches_delete",
    "gs_branches_rename",
    "gs_branches_configure_tracking",
    "gs_branches_push_selected",
    "gs_branches_push_all",
    "gs_branches_merge_selected",
    "gs_branches_fetch_and_merge",
    "gs_branches_diff_branch",
    "gs_branches_diff_commit_history",
    "gs_branches_refresh",
    "gs_branches_toggle_remotes",
    "gs_branches_fetch",
    "gs_branches_edit_branch_description",
    "gs_branches_navigate_branch",
    "gs_branches_navigate_to_active_branch",
    "gs_branches_log",
    "gs_branches_log_graph",
)


from typing import Dict, Iterator, List, Optional, TypedDict
from ..git_mixins.active_branch import Commit
from ..git_mixins.branches import Branch


class BranchViewState(TypedDict, total=False):
    git_root: str
    long_status: str
    branches: List[Branch]
    descriptions: Dict[str, str]
    remotes: Dict[str, str]
    recent_commits: List[Commit]
    sort_by_recent: bool
    show_remotes: bool
    show_help: bool


class gs_show_branch(WindowCommand, GitCommand):

    """
    Open a branch dashboard for the active Git repository.
    """

    def run(self):
        ui.show_interface(self.window, self.repo_path, "branch")


class BranchInterface(ui.ReactiveInterface, GitCommand):

    """
    Branch dashboard.
    """

    interface_type = "branch"
    syntax_file = "Packages/GitSavvy/syntax/branch.sublime-syntax"

    template = """\

      ROOT:    {git_root}

      BRANCH:  {branch_status}
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

    subscribe_to = {"branches", "descriptions", "long_status", "recent_commits", "remotes"}
    state = {}  # type: BranchViewState

    def __init__(self, *args, **kwargs):
        self.state = {
            'show_remotes': self.savvy_settings.get("show_remotes_in_branch_dashboard"),
        }
        super().__init__(*args, **kwargs)

    def title(self):
        # type: () -> str
        return "BRANCHES: {}".format(os.path.basename(self.repo_path))

    def refresh_view_state(self):
        # type: () -> None
        enqueue_on_worker(self.get_branches)
        enqueue_on_worker(self.fetch_branch_description_subjects)
        enqueue_on_worker(self.get_latest_commits)
        enqueue_on_worker(self.get_remotes)
        self.view.run_command("gs_update_status")

        self.update_state({
            'git_root': self.short_repo_path,
            'sort_by_recent': self.savvy_settings.get("sort_by_recent_in_branch_dashboard"),
            'show_help': not self.view.settings().get("git_savvy.help_hidden"),
        })

    @contextmanager
    def keep_cursor_on_something(self):
        # type: () -> Iterator[None]
        def cursor_is_on_active_branch():
            sel = self.view.sel()
            return (
                len(sel) == 1
                and self.view.match_selector(
                    sel[0].begin(),
                    "meta.git-savvy.branches.branch.active-branch"
                )
            )
        on_special_symbol = partial(self.cursor_is_on_something, "meta.git-savvy.branches.branch")

        cursor_was_on_active_branch = cursor_is_on_active_branch()
        yield
        active_branch_available = any(b for b in self.state.get("branches", []) if b.active)
        if active_branch_available:
            if cursor_was_on_active_branch and not cursor_is_on_active_branch() or not on_special_symbol():
                self.view.run_command("gs_branches_navigate_to_active_branch")
        else:
            if not on_special_symbol():
                self.view.run_command("gs_branches_navigate_branch")

    @ui.section("branch_status")
    def render_branch_status(self, long_status):
        # type: (str) -> str
        return long_status

    @ui.section("git_root")
    def render_git_root(self, git_root):
        # type: (str) -> str
        return git_root

    @ui.section("head")
    def render_head(self, recent_commits):
        # type: (List[Commit]) -> str
        if not recent_commits:
            return "No commits yet."

        return "{0.hash} {0.message}".format(recent_commits[0])

    @ui.section("branch_list")
    def render_branch_list(self, branches, sort_by_recent):
        # type: (List[Branch], bool) -> str
        local_branches = [branch for branch in branches if branch.is_local]
        if sort_by_recent:
            local_branches = sorted(local_branches, key=lambda branch: -branch.committerdate)
        # Manually get `descriptions` to not delay the first render.
        descriptions = self.state.get("descriptions", {})
        return self._render_branch_list(None, local_branches, descriptions)

    def _render_branch_list(self, remote_name, branches, descriptions):
        # type: (Optional[str], List[Branch], Dict[str, str]) -> str
        remote_name_length = len(remote_name + "/") if remote_name else 0
        return "\n".join(
            "  {indicator} {hash:.7} {name}{tracking}{description}".format(
                indicator="▸" if branch.active else " ",
                hash=branch.commit_hash,
                name=branch.canonical_name[remote_name_length:],
                description=(" " + descriptions.get(branch.canonical_name, "")).rstrip(),
                tracking=(" ({branch}{status})".format(
                    branch=branch.upstream.canonical_name,
                    status=", " + branch.upstream.status if branch.upstream.status else ""
                ) if branch.upstream else "")
            ) for branch in branches
        )

    @ui.section("remotes")
    def render_remotes(self, show_remotes):
        # type: (bool) -> ui.RenderFnReturnType
        return (self.render_remotes_on()
                if show_remotes else
                self.render_remotes_off())

    @ui.section("help")
    def render_help(self, show_help):
        # type: (bool) -> str
        if not show_help:
            return ""
        return self.template_help

    def render_remotes_off(self):
        # type: () -> str
        return "\n\n  ** Press [e] to toggle display of remote branches. **\n"

    @ui.inject_state()
    def render_remotes_on(self, branches, sort_by_recent, remotes):
        # type: (List[Branch], bool, Dict[str, str]) -> ui.RenderFnReturnType
        output_tmpl = "\n"
        render_fns = []
        remote_branches = [b for b in branches if b.is_remote]
        if sort_by_recent:
            remote_branches = sorted(remote_branches, key=lambda branch: -branch.committerdate)

        for remote_name in remotes:
            key = "branch_list_" + remote_name
            output_tmpl += "{" + key + "}\n"
            branches = [b for b in remote_branches if b.canonical_name.startswith(remote_name + "/")]

            @ui.section(key)
            def render(remote_name=remote_name, branches=branches) -> str:
                return self.template_remote.format(
                    remote_name=remote_name,
                    remote_branch_list=self._render_branch_list(remote_name, branches, {})
                )

            render_fns.append(render)

        return output_tmpl, render_fns


class BranchInterfaceCommand(ui.InterfaceCommand):
    interface: BranchInterface

    def get_selected_branch(self):
        # type: () -> Optional[Branch]
        """
        Get a single selected branch. If more then one branch are selected, return (None, None).
        """
        selected_branches = self.get_selected_branches()
        if len(selected_branches) == 1:
            return selected_branches[0]
        else:
            return None

    def get_selected_branches(self, ignore_current_branch=False):
        # type: (bool) -> List[Branch]
        def select_branch(remote_name, branch_name):
            # type: (str, str) -> Branch
            canonical_name = "/".join(filter_((remote_name, branch_name)))
            for branch in self.interface.state["branches"]:
                if branch.canonical_name == canonical_name:
                    return (
                        branch._replace(
                            remote=remote_name,
                            name=branch.canonical_name[len(remote_name + "/"):]
                        )
                        if remote_name else
                        branch
                    )
            raise ValueError(
                "View inconsistent with repository. "
                "No branch data found for '{}'".format(canonical_name)
            )

        LOCAL_BRANCH_NAMES_SELECTOR = (
            "meta.git-savvy.status.section.branch.local "
            "meta.git-savvy.branches.branch.name"
        )
        EXCLUDE_CURRENT_BRANCH = " - meta.git-savvy.branches.branch.active-branch"

        return [
            select_branch("", name)
            for name in ui.extract_by_selector(
                self.view,
                (
                    LOCAL_BRANCH_NAMES_SELECTOR
                    + (EXCLUDE_CURRENT_BRANCH if ignore_current_branch else "")
                )
            )
        ] + [
            select_branch(remote_name, branch_name)
            for remote_name in self.interface.state["remotes"]
            for branch_name in ui.extract_by_selector(
                self.view,
                "meta.git-savvy.branches.branch.name",
                self.region_name_for("branch_list_" + remote_name)
            )
        ]


class CommandForSingleBranch(BranchInterfaceCommand):
    selected_branch: Branch

    def pre_run(self):
        selected_branches = self.get_selected_branches()
        if len(selected_branches) == 1:
            self.selected_branch = selected_branches[0]
        elif len(selected_branches) == 0:
            raise RuntimeError("No branch selected.")
        else:
            raise RuntimeError("Only one branch must be selected.")


class gs_branches_checkout(CommandForSingleBranch):

    """
    Checkout the selected branch.
    """

    def run(self, edit):
        self.window.run_command("gs_checkout_branch", {"branch": self.selected_branch.canonical_name})


class gs_branches_create_new(CommandForSingleBranch):

    """
    Create a new branch from selected branch and checkout.
    """

    def run(self, edit):
        if self.selected_branch.is_remote:
            self.window.run_command("gs_checkout_remote_branch", {"remote_branch": self.selected_branch.canonical_name})
        else:
            self.window.run_command("gs_checkout_new_branch", {"start_point": self.selected_branch.name})


class gs_branches_delete(CommandForSingleBranch):

    """
    Delete selected branch.
    """

    def run(self, edit, force=False):
        if self.selected_branch.is_remote:
            self.delete_remote_branch(self.selected_branch.remote, self.selected_branch.name, force)
        else:
            self.window.run_command("gs_delete_branch", {"branch": self.selected_branch.name, "force": force})

    @util.actions.destructive(description="delete a remote branch")
    @on_worker
    def delete_remote_branch(self, remote, branch_name, force):
        self.window.status_message("Deleting remote branch...")
        self.git(
            "push",
            "--force" if force else None,
            remote,
            ":" + branch_name
        )
        self.window.status_message("Deleted remote branch.")
        util.view.refresh_gitsavvy(self.view)


class gs_branches_rename(CommandForSingleBranch):

    """
    Rename selected branch.
    """

    def run(self, edit):
        if self.selected_branch.is_remote:
            flash(self.view, "Cannot rename remote branches.")
            return

        self.window.run_command("gs_rename_branch", {"branch": self.selected_branch.name})


class gs_branches_configure_tracking(CommandForSingleBranch):

    """
    Configure remote branch to track against for selected branch.
    """

    def run(self, edit):
        if self.selected_branch.is_remote:
            flash(self.view, "Cannot configure remote branches.")
            return

        show_branch_panel(
            partial(self.on_branch_selection, self.selected_branch.name),
            ask_remote_first=True,
            selected_branch=self.selected_branch.name
        )

    def on_branch_selection(self, local_branch, remote_branch):
        self.git("branch", "-u", remote_branch, local_branch)
        util.view.refresh_gitsavvy(self.view)


class gs_branches_push_selected(CommandForSingleBranch):

    """
    Push selected branch to remote.
    """

    def run(self, edit):
        if self.selected_branch.is_remote:
            flash(self.view, "Cannot push remote branches.")
            return

        self.window.run_command("gs_push", {"local_branch_name": self.selected_branch.name})


class gs_branches_push_all(BranchInterfaceCommand):

    """
    Push all branches to remote.
    """

    def run(self, edit):
        show_remote_panel(self.on_remote_selection, allow_direct=True)

    @on_worker
    def on_remote_selection(self, remote):
        self.window.status_message("Pushing all branches to `{}`...".format(remote))
        self.git("push", remote, "--all")
        self.window.status_message("Push successful.")
        util.view.refresh_gitsavvy(self.view)


class gs_branches_merge_selected(BranchInterfaceCommand):

    """
    Merge selected branch into active branch.
    """

    def run(self, edit):
        branches = self.get_selected_branches(ignore_current_branch=True)
        self.action([branch.canonical_name for branch in branches])

    @on_new_thread  # <- A merge could halt to edit the commit message
    def action(self, branches: List[str]):
        try:
            self.merge(branches)
            self.window.status_message("Merge complete.")
        finally:
            util.view.refresh_gitsavvy(self.view)


class gs_branches_fetch_and_merge(BranchInterfaceCommand):

    """
    Fetch from remote and merge fetched branch into active branch.
    """

    def run(self, edit):
        branches = self.get_selected_branches(ignore_current_branch=True)
        self.action(branches)

    @on_new_thread  # <- A merge could halt to edit the commit message
    def action(self, branches: List[Branch]):
        for branch in branches:
            if branch.is_remote:
                self.fetch(branch.remote, branch.name)
            elif branch.upstream:
                self.fetch(
                    remote=branch.upstream.remote,
                    remote_branch=branch.upstream.branch,
                    local_branch=branch.name,
                )

        branches_strings = [branch.canonical_name for branch in branches]
        try:
            self.merge(branches_strings)
            self.window.status_message("Fetch and merge complete.")
        finally:
            util.view.refresh_gitsavvy(self.view)


class gs_branches_diff_branch(CommandForSingleBranch):

    """
    Show a diff comparing the selected branch to the active branch.
    """

    def run(self, edit):
        # type: (object) -> None
        active_branch_name = self.get_current_branch_name()
        self.window.run_command("gs_diff", {
            "base_commit": self.selected_branch.canonical_name,
            "target_commit": active_branch_name,
            "disable_stage": True,
        })


class gs_branches_diff_commit_history(CommandForSingleBranch):

    """
    Show a view of all commits diff between branches.
    """

    def run(self, edit):
        # type: (object) -> None
        target_commit = self.get_current_branch_name()
        self.window.run_command("gs_compare_commit", {
            "base_commit": self.selected_branch.canonical_name,
            "target_commit": target_commit
        })


class gs_branches_refresh(BranchInterfaceCommand):

    """
    Refresh the branch dashboard.
    """

    def run(self, edit):
        util.view.refresh_gitsavvy(self.view)


class gs_branches_toggle_remotes(BranchInterfaceCommand):

    """
    Toggle display of the remote branches.
    """

    def run(self, edit, show=None):
        if show is None:
            self.interface.update_state({"show_remotes": not self.interface.state["show_remotes"]})
        else:
            self.interface.update_state({"show_remotes": show})
        self.interface.render()


class gs_branches_fetch(BranchInterfaceCommand):

    """
    Prompt for remote and fetch branches.
    """

    def run(self, edit):
        self.window.run_command("gs_fetch")


class gs_branches_edit_branch_description(CommandForSingleBranch):

    """
    Save a description for the selected branch
    """

    def run(self, edit):
        if self.selected_branch.is_remote:
            flash(self.view, "Cannot edit descriptions for remote branches.")

        current_description = self.git(
            "config",
            "branch.{}.description".format(self.selected_branch.name),
            throw_on_error=False
        ).strip(" \n")

        show_single_line_input_panel(
            "Enter new description (for {}):".format(self.selected_branch.name),
            current_description,
            partial(self.on_entered_description, self.selected_branch.name)
        )

    def on_entered_description(self, branch_name: str, new_description: str):
        self.git(
            "config",
            "--unset" if not new_description else None,
            "branch.{}.description".format(branch_name),
            new_description.strip("\n")
        )
        util.view.refresh_gitsavvy(self.view)


class gs_branches_navigate_branch(GsNavigate):

    """
    Move cursor to the next (or previous) selectable branch in the dashboard.
    """
    offset = 0

    def get_available_regions(self):
        return self.view.find_by_selector("constant.other.git-savvy.branches.branch.sha1")


class gs_branches_navigate_to_active_branch(GsNavigate):

    """
    Move cursor to the active branch.
    """
    offset = 0

    def get_available_regions(self):
        return self.view.find_by_selector(
            "meta.git-savvy.branches.branch.active-branch constant.other.git-savvy.branches.branch.sha1")


class gs_branches_log(LogMixin, CommandForSingleBranch):

    """
    Show log for the selected branch.
    """

    def run_async(self, **kwargs):
        super().run_async(branch=self.selected_branch.canonical_name)


class gs_branches_log_graph(CommandForSingleBranch):

    """
    Show log graph for the selected branch.
    """

    def run(self, edit):
        self.window.run_command('gs_graph', {
            'all': True,
            'branches': [self.selected_branch.canonical_name],
            'follow': self.selected_branch.canonical_name
        })
