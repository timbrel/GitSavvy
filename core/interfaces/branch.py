from contextlib import contextmanager
from functools import partial
import os
import threading

import sublime
from sublime_plugin import WindowCommand

from .status import distinct_until_state_changed
from ...common import ui, util
from ..commands import GsNavigate
from ..commands.log import LogMixin
from ..git_command import GitCommand
from ..ui_mixins.quick_panel import show_remote_panel, show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel
from GitSavvy.core import store
from GitSavvy.core.fns import filter_
from GitSavvy.core.utils import flash
from GitSavvy.core.runtime import on_worker


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


MYPY = False
if MYPY:
    from typing import Dict, List, Optional, TypedDict
    from ..git_mixins.active_branch import Commit
    from ..git_mixins.branches import Branch

    BranchViewState = TypedDict(
        "BranchViewState",
        {
            "git_root": str,
            "long_status": str,
            "branches": List[Branch],
            "descriptions": Dict[str, str],
            "remotes": Dict[str, str],
            "recent_commits": List[Commit],
            "show_remotes": bool,
            "show_help": bool,
        },
        total=False
    )


class gs_show_branch(WindowCommand, GitCommand):

    """
    Open a branch dashboard for the active Git repository.
    """

    def run(self):
        ui.show_interface(self.window, self.repo_path, "branch")


class BranchInterface(ui.Interface, GitCommand):

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

    def __init__(self, *args, **kwargs):
        self._lock = threading.Lock()
        self.state = {
            'git_root': '',
            'long_status': '',
            'recent_commits': [],
            'branches': [],
            'descriptions': {},
            'remotes': {},
            'show_remotes': self.savvy_settings.get("show_remotes_in_branch_dashboard"),
            'show_help': True,
        }  # type: BranchViewState
        super().__init__(*args, **kwargs)

    def title(self):
        return "BRANCHES: {}".format(os.path.basename(self.repo_path))

    def refresh_view_state(self):
        sort_by_recent = self.savvy_settings.get("sort_by_recent_in_branch_dashboard")
        for thunk in (
            lambda: {'recent_commits': self.get_latest_commits()},
            lambda: {
                'branches': self.get_branches(sort_by_recent=sort_by_recent),
                'descriptions': self.fetch_branch_description_subjects(),
            },
            lambda: {'remotes': self.get_remotes()},
        ):
            sublime.set_timeout_async(
                partial(self.update_state, thunk, then=self.just_render)
            )

        self.view.run_command("gs_update_status")
        # These are cheap to compute, so we just do it!
        state = store.current_state(self.repo_path)
        status = state.get("status")
        if status:
            self.update_state(status._asdict())
        self.update_state({
            'git_root': self.short_repo_path,
            'branches': state.get("branches", []),
            'recent_commits': state.get("recent_commits", []),
            'descriptions': state.get("descriptions", {}),
            'show_help': not self.view.settings().get("git_savvy.help_hidden"),
        })

    def update_state(self, data, then=None):
        """Update internal view state and maybe invoke a callback.

        `data` can be a mapping or a callable ("thunk") which returns
        a mapping.

        Note: We invoke the "sink" without any arguments. TBC.
        """
        if callable(data):
            data = data()

        with self._lock:
            self.state.update(data)

        if callable(then):
            then()

    def render(self):
        """Refresh view state and render."""
        self.refresh_view_state()
        self.just_render()

    @distinct_until_state_changed
    def just_render(self):
        content, regions = self._render_template()
        with self.keep_cursor_on_something():
            self.draw(self.title(), content, regions)

    @contextmanager
    def keep_cursor_on_something(self):
        def cursor_is_on_active_branch():
            sel = self.view.sel()
            return (
                len(sel) == 1
                and self.view.match_selector(
                    sel[0].begin(),
                    "meta.git-savvy.branches.branch.active-branch"
                )
            )

        cursor_was_on_active_branch = cursor_is_on_active_branch()
        yield
        if cursor_was_on_active_branch and not cursor_is_on_active_branch():
            self.view.run_command("gs_branches_navigate_to_active_branch")

    def on_status_update(self, _repo_path, state):
        try:
            new_state = state["status"]._asdict()
        except KeyError:
            new_state = {}
        new_state["branches"] = state.get("branches")
        new_state["recent_commits"] = state.get("recent_commits")
        new_state["descriptions"] = state.get("descriptions")
        self.update_state(new_state, then=self.just_render)

    def on_create(self):
        self._unsubscribe = store.subscribe(
            self.repo_path,
            {"status", "branches", "recent_commits", "descriptions"},
            self.on_status_update
        )

    def on_close(self):
        self._unsubscribe()

    def on_new_dashboard(self):
        self.view.run_command("gs_branches_navigate_to_active_branch")

    @ui.section("branch_status")
    def render_branch_status(self):
        return self.state['long_status']

    @ui.section("git_root")
    def render_git_root(self):
        return self.state['git_root']

    @ui.section("head")
    def render_head(self):
        recent_commits = self.state['recent_commits']
        if not recent_commits:
            return "No commits yet."

        return "{0.hash} {0.message}".format(recent_commits[0])

    @ui.section("branch_list")
    def render_branch_list(self):
        # type: () -> str
        branches = [branch for branch in self.state["branches"] if not branch.is_remote]
        return self._render_branch_list(None, branches)

    def _render_branch_list(self, remote_name, branches):
        # type: (Optional[str], List[Branch]) -> str
        remote_name_l = len(remote_name + "/") if remote_name else 0
        return "\n".join(
            "  {indicator} {hash:.7} {name}{tracking}{description}".format(
                indicator="▸" if branch.active else " ",
                hash=branch.commit_hash,
                name=branch.canonical_name[remote_name_l:],
                description=(" " + self.state["descriptions"].get(branch.canonical_name, "")).rstrip(),
                tracking=(" ({branch}{status})".format(
                    branch=branch.upstream.canonical_name,
                    status=", " + branch.upstream.status if branch.upstream.status else ""
                ) if branch.upstream else "")
            ) for branch in branches
        )

    @ui.section("remotes")
    def render_remotes(self):
        return (self.render_remotes_on()
                if self.state["show_remotes"] else
                self.render_remotes_off())

    @ui.section("help")
    def render_help(self):
        show_help = self.state['show_help']
        if not show_help:
            return ""
        return self.template_help

    def render_remotes_off(self):
        return "\n\n  ** Press [e] to toggle display of remote branches. **\n"

    def render_remotes_on(self):
        output_tmpl = "\n"
        render_fns = []
        remote_branches = [b for b in self.state["branches"] if b.is_remote]

        for remote_name in self.state["remotes"]:
            key = "branch_list_" + remote_name
            output_tmpl += "{" + key + "}\n"
            branches = [b for b in remote_branches if b.canonical_name.startswith(remote_name + "/")]

            @ui.section(key)
            def render(remote_name=remote_name, branches=branches):
                return self.template_remote.format(
                    remote_name=remote_name,
                    remote_branch_list=self._render_branch_list(remote_name, branches)
                )

            render_fns.append(render)

        return output_tmpl, render_fns


class BranchInterfaceCommand(ui.InterfaceCommand):
    interface_type = BranchInterface
    interface = None  # type: BranchInterface

    def get_selected_branch(self):
        # type: () -> Optional[Branch]
        """
        Get a single selected branch. If more then one branch are selected, return (None, None).
        """
        selected_branches = self.get_selected_branches()
        if selected_branches and len(selected_branches) == 1:
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


class gs_branches_checkout(BranchInterfaceCommand):

    """
    Checkout the selected branch.
    """

    @on_worker
    def run(self, edit):
        branch = self.get_selected_branch()
        if not branch:
            return

        self.window.run_command("gs_checkout_branch", {"branch": branch.canonical_name})


class gs_branches_create_new(BranchInterfaceCommand):

    """
    Create a new branch from selected branch and checkout.
    """

    @on_worker
    def run(self, edit):
        branch = self.get_selected_branch()
        if not branch:
            return

        if branch.is_remote:
            self.window.run_command("gs_checkout_remote_branch", {"remote_branch": branch.canonical_name})
        else:
            self.window.run_command("gs_checkout_new_branch", {"start_point": branch.name})


class gs_branches_delete(BranchInterfaceCommand):

    """
    Delete selected branch.
    """

    @on_worker
    def run(self, edit, force=False):
        self.force = force
        branch = self.get_selected_branch()
        if not branch:
            return

        if branch.is_remote:
            self.delete_remote_branch(branch.remote, branch.name, self.window)
        else:
            self.window.run_command("gs_delete_branch", {"branch": branch.name, "force": self.force})

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


class gs_branches_rename(BranchInterfaceCommand):

    """
    Rename selected branch.
    """

    @on_worker
    def run(self, edit):
        branch = self.get_selected_branch()
        if not branch:
            return
        if branch.is_remote:
            flash(self.view, "Cannot rename remote branches.")
            return

        self.window.run_command("gs_rename_branch", {"branch": branch.name})


class gs_branches_configure_tracking(BranchInterfaceCommand):

    """
    Configure remote branch to track against for selected branch.
    """

    @on_worker
    def run(self, edit):
        branch = self.get_selected_branch()
        if not branch:
            return
        if branch.is_remote:
            flash(self.view, "Cannot configure remote branches.")
            return

        self.local_branch = branch.name

        show_branch_panel(
            self.on_branch_selection,
            ask_remote_first=True,
            selected_branch=branch.name
        )

    def on_branch_selection(self, branch):
        self.git("branch", "-u", branch, self.local_branch)
        util.view.refresh_gitsavvy(self.view)


class gs_branches_push_selected(BranchInterfaceCommand):

    """
    Push selected branch to remote.
    """

    @on_worker
    def run(self, edit):
        branch = self.get_selected_branch()
        if not branch:
            return
        if branch.is_remote:
            flash(self.view, "Cannot push remote branches.")
            return

        self.window.run_command("gs_push", {"local_branch_name": branch.name})


class gs_branches_push_all(BranchInterfaceCommand):

    """
    Push all branches to remote.
    """

    @on_worker
    def run(self, edit):
        show_remote_panel(self.on_remote_selection, allow_direct=True)

    def on_remote_selection(self, remote):
        self.window.status_message("Pushing all branches to `{}`...".format(remote))
        self.git("push", remote, "--all")
        self.window.status_message("Push successful.")
        util.view.refresh_gitsavvy(self.view)


class gs_branches_merge_selected(BranchInterfaceCommand):

    """
    Merge selected branch into active branch.
    """

    @on_worker
    def run(self, edit):
        branches = self.get_selected_branches(ignore_current_branch=True)
        branches_strings = [branch.canonical_name for branch in branches]
        try:
            self.merge(branches_strings)
            self.window.status_message("Merge complete.")
        finally:
            util.view.refresh_gitsavvy(self.view)


class gs_branches_fetch_and_merge(BranchInterfaceCommand):

    """
    Fetch from remote and merge fetched branch into active branch.
    """

    @on_worker
    def run(self, edit):
        branches = self.get_selected_branches(ignore_current_branch=True)

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


class gs_branches_diff_branch(BranchInterfaceCommand):

    """
    Show a diff comparing the selected branch to the active branch.
    """

    @on_worker
    def run(self, edit):
        # type: (object) -> None
        branch = self.get_selected_branch()
        if not branch:
            return
        self.show_diff(branch.canonical_name)

    def show_diff(self, comparison_branch_name):
        # type: (str) -> None
        active_branch_name = self.get_current_branch_name()
        self.window.run_command("gs_diff", {
            "base_commit": comparison_branch_name,
            "target_commit": active_branch_name,
            "disable_stage": True,
            "title": "DIFF: {}..{}".format(comparison_branch_name, active_branch_name)
        })


class gs_branches_diff_commit_history(BranchInterfaceCommand):

    """
    Show a view of all commits diff between branches.
    """

    @on_worker
    def run(self, edit):
        # type: (object) -> None
        branch = self.get_selected_branch()
        if not branch:
            return
        self.show_commits(branch.canonical_name)

    def show_commits(self, base_commit):
        # type: (str) -> None
        target_commit = self.get_current_branch_name()
        self.window.run_command("gs_compare_commit", {
            "base_commit": base_commit,
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


class gs_branches_edit_branch_description(BranchInterfaceCommand):

    """
    Save a description for the selected branch
    """

    @on_worker
    def run(self, edit):
        branch = self.get_selected_branch()
        if not branch:
            return
        if branch.is_remote:
            flash(self.view, "Cannot edit descriptions for remote branches.")

        self.branch_name = branch.name

        current_description = self.git(
            "config",
            "branch.{}.description".format(self.branch_name),
            throw_on_error=False
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


class gs_branches_navigate_branch(GsNavigate):

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


class gs_branches_navigate_to_active_branch(GsNavigate):

    """
    Move cursor to the active branch.
    """

    def get_available_regions(self):
        return [
            branch_region
            for region in self.view.find_by_selector(
                "meta.git-savvy.branches.branch.active-branch meta.git-savvy.branches.branch.sha1"
            )
            for branch_region in self.view.lines(region)]


class gs_branches_log(LogMixin, BranchInterfaceCommand):

    """
    Show log for the selected branch.
    """

    def run_async(self, **kwargs):
        branch = self.get_selected_branch()
        if not branch:
            return

        super().run_async(branch=branch.canonical_name)


class gs_branches_log_graph(BranchInterfaceCommand):

    """
    Show log graph for the selected branch.
    """

    def run(self, edit):
        branch = self.get_selected_branch()
        if not branch:
            return

        self.window.run_command('gs_graph', {
            'all': True,
            'branches': [branch.canonical_name],
            'follow': branch.canonical_name
        })
