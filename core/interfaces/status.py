from functools import partial
from contextlib import contextmanager
import os

import sublime
from sublime_plugin import EventListener, WindowCommand

from ..git_mixins.status import FileStatus
from ..git_mixins.active_branch import format_and_limit
from ..commands import GsNavigate
from ...common import ui
from ..git_command import GitCommand, GitSavvyError
from ...common import util
from GitSavvy.core.fns import filter_
from GitSavvy.core.runtime import enqueue_on_worker
from GitSavvy.core.utils import noop, show_actions_panel


__all__ = (
    "gs_show_status",
    "StatusViewContextSensitiveHelpEventListener",
    "gs_status_open_file",
    "gs_status_open_file_on_remote",
    "gs_status_diff_inline",
    "gs_status_diff",
    "gs_status_stage_file",
    "gs_status_unstage_file",
    "gs_status_discard_changes_to_file",
    "gs_status_stage_all_files",
    "gs_status_stage_all_files_with_untracked",
    "gs_status_unstage_all_files",
    "gs_status_discard_all_changes",
    "gs_status_ignore_file",
    "gs_status_ignore_pattern",
    "gs_status_stash",
    "gs_status_launch_merge_tool",
    "gs_status_use_commit_version",
    "gs_status_use_base_version",
    "gs_status_navigate_file",
    "gs_status_navigate_goto",
)


from typing import Iterable, Iterator, List, Optional, TypedDict
from ..git_mixins.active_branch import Commit
from ..git_mixins.branches import Branch
from ..git_mixins.stash import Stash
from ..git_mixins.status import HeadState, WorkingDirState


class StatusViewState(TypedDict, total=False):
    status: WorkingDirState
    long_status: str
    git_root: str
    show_help: bool
    help_context: Optional[str]
    head: HeadState
    branches: List[Branch]
    recent_commits: List[Commit]
    stashes: List[Stash]
    skipped_files: List[str]


# Expected
#  - common/commands/view_manipulation.py
#    common/ui.py
#    core/commands/commit_compare.py -> core/commands/commit_compare_foo.py
# But do not match our stashes or anything from our help
#    (1) log git start/stop
#           [t] create stash
EXTRACT_FILENAME_RE = (
    r"^(?:    .+ -> |  [ -] (?!\(\d+\) ))"
    #     ^ leading 4 spaces
    #         ^ a filename
    #            ^ marker indicating a rename/move
    #               ^ OR
    #                ^ leading 4 spaces or two spaces and our deleted marker
    #                       ^^^^^^^^^^^ but be aware to *not* match stashes
    r"(?!Your working directory is clean\.)"
    #   ^ be aware to *not* match this message which otherwise fulfills our
    #     filename matcher
    r"(\S.*)$"
    # ^^^^^^ the actual filename matcher
    # Note: A filename cannot start with a space (which is luckily true anyway)
    # otherwise our naive `.*` could consume only whitespace.
)
ITEMS_IN_THE_RECENT_LIST = 5


class gs_show_status(WindowCommand, GitCommand):

    """
    Open a status view for the active git repository.
    """

    def run(self):
        ui.show_interface(self.window, self.repo_path, "status")


class StatusViewContextSensitiveHelpEventListener(EventListener):
    def on_selection_modified_async(self, view):
        interface = ui.interfaces.get(view.id())
        if not isinstance(interface, StatusInterface):
            return

        frozen_sel = list(view.sel())
        if all(view.match_selector(s.a, "constant.other.git-savvy.sha1") for s in frozen_sel):
            next_state = "on_commit"
        elif all(view.match_selector(s.a, "meta.git-savvy.status.section.added") for s in frozen_sel):
            next_state = "on_added"
        else:
            next_state = None

        current_state = interface.state.get("help_context")
        if next_state != current_state:
            interface.state["help_context"] = next_state
            interface.just_render(keep_cursor_on_something=False)


class StatusInterface(ui.ReactiveInterface, GitCommand):

    """
    Status dashboard.
    """

    interface_type = "status"
    syntax_file = "Packages/GitSavvy/syntax/status.sublime-syntax"

    template = """\

      ROOT:    {git_root}

      BRANCH:  {branch_status}
      HEAD:    {head}

    {< unstaged_files}
    {< untracked_files}
    {< added_files}
    {< staged_files}
    {< merge_conflicts}
    {< no_status_message}
    {< stashes}
    {< skipped_files}
    {< help}
    """

    _template_help = """
      #############                         #############
      ## ACTIONS ##                         ## STASHES ##
      #############                         #############

      [c] commit                            [t][a] apply stash
      [C] commit, including unstaged        [t][p] pop stash
      [m] amend previous commit             [o]    open stash
      [p] push current branch               [t][c] create stash
      [P] pull current branch               [t][u] create stash including untracked files
                                            [t][g] create stash of staged changes only
      [I] add .gitignore pattern            [t][d] drop stash

      [M] launch external merge tool
      [B] abort merge

      ###########
      ## OTHER ##
      ###########

      [g]         show graph repo history
      [?]         toggle this help menu
      [tab]       transition to next dashboard
      [SHIFT-tab] transition to previous dashboard
      [.]         move cursor to next file
      [,]         move cursor to previous file
    {conflicts_bindings}
    -
    """

    template_help = """
      ###################                   ###############
      ## SELECTED FILE ##                   ## ALL FILES ##
      ###################                   ###############

      [o] open file                         [S] stage all unstaged files
      [s] stage file                        [A] stage all unstaged and untracked files
      [u] unstage file                      [U] unstage all staged files
      [d] discard changes to file           [D] discard all unstaged changes
      [i] skip/unskip file
      [h] open file in browser

      [l] diff file inline                  [f] diff all files
      [e] diff file                         [F] diff all cached files
    """ + _template_help

    template_help_on_added = template_help.replace(
        "[u] unstage file ",
        "[u] unadd file   "
    )

    template_help_on_commit = """
      #####################                 ###############
      ## SELECTED COMMIT ##                 ## ALL FILES ##
      #####################                 ###############

      [o] show commit                       [S] stage all unstaged files
                                            [A] stage all unstaged and untracked files
                                            [U] unstage all staged files
                                            [D] discard all unstaged changes



                                            [f] diff all files
                                            [F] diff all cached files
    """ + _template_help

    conflicts_keybindings = ui.indent_by_2("""
    ###############
    ## CONFLICTS ##
    ###############

    [y] use version from your commit
    [b] use version from the base
    """)

    template_staged = """
      STAGED:
    {}
    """

    template_unstaged = """
      UNSTAGED:
    {}
    """

    template_added = """
      ADDED:
    {}
    """

    template_untracked = """
      UNTRACKED:
    {}
    """

    template_merge_conflicts = """
      MERGE CONFLICTS:
    {}
    """

    template_skipped = """
      SKIPPED:
    {}
    """

    template_stashes = """
      STASHES:
    {}
    """

    subscribe_to = {
        "branches", "head", "long_status", "recent_commits", "skipped_files", "stashes", "status"
    }
    state: StatusViewState

    def initial_state(self):
        return {
            'help_context': None,
        }

    def title(self):
        # type: () -> str
        return "STATUS: {}".format(os.path.basename(self.repo_path))

    def refresh_view_state(self):
        # type: () -> None
        """Update all view state.

        Note: For every possible long running process, we enqueue a task
        in a worker thread. We re-render as soon as we receive meaningful
        data which implies that the view is only _eventual_ consistent
        with the real world.
        """
        enqueue_on_worker(self.get_latest_commits)
        enqueue_on_worker(self.get_branches)
        enqueue_on_worker(self.get_stashes)
        enqueue_on_worker(self.get_skipped_files)
        self.view.run_command("gs_update_status")

        self.update_state({
            'git_root': self.short_repo_path,
            'show_help': not self.view.settings().get("git_savvy.help_hidden"),
        })

    @contextmanager
    def keep_cursor_on_something(self):
        # type: () -> Iterator[None]
        on_a_file = partial(self.cursor_is_on_something, 'meta.git-savvy.entity.filename')
        on_special_symbol = partial(
            self.cursor_is_on_something,
            'meta.git-savvy.section.body.row'
            ', constant.other.git-savvy.sha1'
        )

        was_on_a_file = on_a_file()
        yield
        if was_on_a_file and not on_a_file() or not on_special_symbol():
            self.view.run_command("gs_status_navigate_goto")

    def refresh_repo_status_and_render(self):
        # type: () -> None
        """Refresh `git status` state and render.

        Most actions in the status dashboard only affect the `git status`.
        So instead of calling `render` it is a good optimization to just
        ask this method if appropriate.
        """
        self.update_working_dir_status()

    def after_view_creation(self, view):
        # type: (sublime.View) -> None
        view.settings().set("result_file_regex", EXTRACT_FILENAME_RE)
        view.settings().set("result_base_dir", self.repo_path)

    @ui.section("branch_status")
    def render_branch_status(self, long_status):
        # type: (str) -> str
        return long_status

    @ui.section("git_root")
    def render_git_root(self, git_root):
        # type: (str) -> str
        return git_root

    @ui.section("head")
    def render_head(self, recent_commits, head, branches):
        # type: (List[Commit], HeadState, List[Branch]) -> str
        if not recent_commits:
            return "No commits yet."

        current_upstream = head.remote
        branches = branches
        return "\n           ".join(
            format_and_limit(recent_commits, ITEMS_IN_THE_RECENT_LIST, current_upstream, branches)
        )

    @ui.section("staged_files")
    def render_staged_files(self, status):
        # type: (WorkingDirState) -> str
        staged_files = status.staged_files
        if not staged_files:
            return ""

        def get_path(file_status):
            """ Display full file_status path, including path_alt if exists """
            if file_status.path_alt:
                return '{} -> {}'.format(file_status.path_alt, file_status.path)
            return file_status.path

        return self.template_staged.format("\n".join(
            "  {} {}".format("-" if f.index_status == "D" else " ", get_path(f))
            for f in staged_files
        ))

    @ui.section("unstaged_files")
    def render_unstaged_files(self, status):
        # type: (WorkingDirState) -> str
        unstaged_files = [f for f in status.unstaged_files if f.working_status != "A"]
        if not unstaged_files:
            return ""

        def get_path(file_status):
            """ Display full file_status path, including path_alt if exists """
            if file_status.path_alt:
                return '{} -> {}'.format(file_status.path_alt, file_status.path)
            return file_status.path

        return self.template_unstaged.format("\n".join(
            "  {} {}".format("-" if f.working_status == "D" else " ", get_path(f))
            for f in unstaged_files
        ))

    @ui.section("added_files")
    def render_added_files(self, status):
        # type: (WorkingDirState) -> str
        added_files = [f for f in status.unstaged_files if f.working_status == "A"]
        if not added_files:
            return ""

        return self.template_added.format(
            "\n".join("    " + f.path for f in added_files))

    @ui.section("untracked_files")
    def render_untracked_files(self, status):
        # type: (WorkingDirState) -> str
        untracked_files = status.untracked_files
        if not untracked_files:
            return ""

        return self.template_untracked.format(
            "\n".join("    " + f.path for f in untracked_files))

    @ui.section("merge_conflicts")
    def render_merge_conflicts(self, status):
        # type: (WorkingDirState) -> str
        merge_conflicts = status.merge_conflicts
        if not merge_conflicts:
            return ""
        return self.template_merge_conflicts.format(
            "\n".join("    " + f.path for f in merge_conflicts))

    @ui.section("skipped_files")
    def render_skipped_files(self, skipped_files):
        # type: (List[str]) -> str
        if not skipped_files:
            return ""
        return self.template_skipped.format(
            "\n".join("    " + f for f in skipped_files))

    @ui.section("conflicts_bindings")
    def render_conflicts_bindings(self, status):
        # type: (WorkingDirState) -> str
        return self.conflicts_keybindings if status.merge_conflicts else ""

    @ui.section("no_status_message")
    def render_no_status_message(self, status):
        # type: (WorkingDirState) -> str
        return (
            "\n    Your working directory is clean.\n"
            if status.is_clean
            else ""
        )

    @ui.section("stashes")
    def render_stashes(self, stashes):
        # type: (List[Stash]) -> str
        if not stashes:
            return ""

        return self.template_stashes.format("\n".join(
            "    ({}) {}".format(stash.id, stash.description) for stash in stashes))

    @ui.section("help")
    def render_help(self, show_help, help_context):
        # type: (bool, Optional[str]) -> str
        if not show_help:
            return ""

        if help_context == "on_commit":
            return self.template_help_on_commit.format(conflicts_bindings=self.render_conflicts_bindings())
        if help_context == "on_added":
            return self.template_help_on_added.format(conflicts_bindings=self.render_conflicts_bindings())
        return self.template_help.format(conflicts_bindings=self.render_conflicts_bindings())


class StatusInterfaceCommand(ui.InterfaceCommand):
    interface: StatusInterface

    def _get_subjects_selector(self, sections):
        # type: (Iterable[str]) -> str
        return ", ".join(
            'meta.git-savvy.status.section.{} meta.git-savvy.status.subject'.format(section)
            for section in sections
        )

    def get_selected_subjects(self, *sections):
        # type: (str) -> List[str]
        return ui.extract_by_selector(self.view, self._get_subjects_selector(sections))

    def get_selected_files(self, base_path, *sections):
        # type: (str, str) -> List[str]
        if not sections:
            sections = ('staged', 'unstaged', 'untracked', 'added', 'merge-conflicts', 'skipped')

        make_abs_path = partial(os.path.join, base_path)
        return [
            os.path.normpath(make_abs_path(filename))
            for filename in self.get_selected_subjects(*sections)
        ]


class gs_status_open_file(StatusInterfaceCommand):

    """
    For every file that is selected or under a cursor, open a that
    file in a new view.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        for fpath in self.get_selected_files(self.repo_path):
            self.window.open_file(fpath)


class gs_status_open_file_on_remote(StatusInterfaceCommand):

    """
    For every file that is selected or under a cursor, open a new browser
    window to that file on GitHub.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        file_paths = self.get_selected_subjects('staged', 'unstaged', 'merge-conflicts')
        if file_paths:
            self.view.run_command("gs_github_open_file_in_browser", {"fpath": file_paths})


class gs_status_diff_inline(StatusInterfaceCommand):

    """
    For every file selected or under a cursor, open a new inline-diff view for
    that file.  If the file is staged, open the inline-diff in cached mode.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        repo_path = self.repo_path
        non_cached_files = self.get_selected_files(repo_path, 'unstaged', 'merge-conflicts')
        cached_files = self.get_selected_files(repo_path, 'staged')

        enqueue_on_worker(
            self.load_inline_diff_views, self.window, non_cached_files, cached_files
        )

    def load_inline_diff_views(self, window, non_cached_files, cached_files):
        # type: (sublime.Window, List[str], List[str]) -> None
        for fpath in non_cached_files:
            syntax = util.file.guess_syntax_for_file(window, fpath)
            window.run_command("gs_inline_diff_open", {
                "repo_path": self.repo_path,
                "file_path": fpath,
                "syntax": syntax,
                "cached": False,
            })

        for fpath in cached_files:
            syntax = util.file.guess_syntax_for_file(window, fpath)
            window.run_command("gs_inline_diff_open", {
                "repo_path": self.repo_path,
                "file_path": fpath,
                "syntax": syntax,
                "cached": True,
            })


class gs_status_diff(StatusInterfaceCommand):

    """
    For every file selected or under a cursor, open a new diff view for
    that file.  If the file is staged, open the diff in cached mode.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        repo_path = self.repo_path
        untracked_files = self.get_selected_files(repo_path, 'untracked')
        non_cached_files = self.get_selected_files(
            repo_path, 'unstaged', 'added', 'merge-conflicts')
        cached_files = self.get_selected_files(repo_path, 'staged')

        enqueue_on_worker(
            self.load_diff_windows, self.window, non_cached_files, cached_files, untracked_files
        )

    def load_diff_windows(self, window, non_cached_files, cached_files, untracked_files):
        # type: (sublime.Window, List[str], List[str], List[str]) -> None
        if untracked_files:
            self.intent_to_add(*untracked_files)

        for fpath in non_cached_files + untracked_files:
            window.run_command("gs_diff", {
                "file_path": fpath,
                "in_cached_mode": False,
            })

        for fpath in cached_files:
            window.run_command("gs_diff", {
                "file_path": fpath,
                "in_cached_mode": True,
            })


class gs_status_stage_file(StatusInterfaceCommand):

    """
    For every file that is selected or under a cursor, if that file is
    unstaged, stage it.
    """

    def run(self, edit, check=True):
        # type: (sublime.Edit, bool) -> None
        files_with_merge_conflicts = self.get_selected_subjects('merge-conflicts')
        if check and files_with_merge_conflicts:
            failed_files = self.check_for_conflict_markers(files_with_merge_conflicts)
            if failed_files:
                show_actions_panel(self.window, [
                    noop(
                        "Abort, '{}' has unresolved conflicts.".format(next(iter(failed_files)))
                        if len(failed_files) == 1 else
                        "Abort, some files have unresolved conflicts."
                    ),
                    (
                        "Stage anyway.",
                        lambda: self.view.run_command("gs_status_stage_file", {
                            "check": False
                        })
                    )
                ])
                return

        file_paths = (
            self.get_selected_subjects('unstaged', 'untracked', 'added')
            + files_with_merge_conflicts
        )
        if file_paths:
            self.stage_file(*file_paths, force=False)
            self.window.status_message("Staged files successfully.")
            self.interface.refresh_repo_status_and_render()


class gs_status_unstage_file(StatusInterfaceCommand):

    """
    For every file that is selected or under a cursor, if that file is
    staged, unstage it.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        files_to_unstage = self.get_selected_subjects('staged', 'merge-conflicts')
        if files_to_unstage:
            self.unstage_file(*files_to_unstage)

        files_to_unadd = self.get_selected_subjects('added')
        if files_to_unadd:
            self.undo_intent_to_add(*files_to_unadd)

        if files_to_unstage or files_to_unadd:
            if not files_to_unadd:
                self.window.status_message("Unstaged files successfully.")
            else:
                self.window.status_message("Reset files successfully.")
            self.interface.refresh_repo_status_and_render()


class gs_status_discard_changes_to_file(StatusInterfaceCommand):

    """
    For every file that is selected or under a cursor, if that file is
    unstaged, reset the file to HEAD.  If it is untracked, delete it.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        untracked_files = self.discard_untracked()
        unstaged_files = self.discard_unstaged()
        if untracked_files or unstaged_files:
            self.window.status_message("Successfully discarded changes.")
            self.interface.refresh_repo_status_and_render()

        if self.get_selected_subjects('staged'):
            self.window.status_message("Staged files cannot be discarded.  Unstage them first.")

    def discard_untracked(self):
        # type: () -> Optional[List[str]]
        file_paths = self.get_selected_subjects('untracked')

        @util.actions.destructive(description="discard one or more untracked files")
        def do_discard():
            self.discard_untracked_file(*file_paths)
            return file_paths

        if file_paths:
            return do_discard()
        return None

    def discard_unstaged(self):
        # type: () -> Optional[List[str]]

        @util.actions.destructive(description="discard one or more unstaged files")
        def do_discard(file_paths: List[str]):
            try:
                self.checkout_file(*file_paths)
            except GitSavvyError as err:
                # For the error message see the lengthy comment below.
                if "did not match any file(s) known to git" in err.stderr:
                    pass
                else:
                    raise

        file_paths = self.get_selected_subjects('unstaged', 'merge-conflicts')
        if file_paths:
            selected_unstaged_files = [
                f for f in self.interface.state["status"].unstaged_files
                if f.path in file_paths
            ]
            # "R"-enamed files in the unstaged section are not your standard rename!
            # See: https://stackoverflow.com/questions/77950918/how-to-produce-the-file-status-r-in-git
            # We "un-intent" basically to split the " R" up.  After that we should have
            # two files in the status: " D" <path_alt> and "??" <path>.  The intention
            # is to not have any data loss.  (At the cost of possibly having more clicks
            # to make.)
            # NOTE: We also `undo_intent_to_add` for " A" but we do this in `gs_status_unstage_file`
            #       because we show these entries in a separate section and discarding sounds too
            #       destructive for that action.
            # NOTE: For " D" we typically want to restore the file, aka undelete behavior.
            #       This is what `do_discard` here does.
            #       However, it could be that the file is unknown and git throws:
            #       "error: pathspec '<path>' did not match any file(s) known to git"
            #       Then `reset -- <path>` like in `undo_intent_to_add` would have been
            #       the better operation because it handles these cases without throwing.
            #       But we cannot tell before we try!  And git does everything well enough
            #       even if it raises the error.
            #       Keep in mind that we literally cannot undelete in those case as the content
            #       of these files is untracked and not in the index.  (`--intent-to-add` just
            #       adds an empty file in the index!) So cleaning the index is the best we can do.
            #       The user can also just stage, as in: acknowledge, such an entry and it will just
            #       disappear (because there is no further content to track).
            files_to_unintent = [
                f.path for f in selected_unstaged_files
                if f.working_status == "R"
            ]
            if files_to_unintent:
                self.undo_intent_to_add(*files_to_unintent)
            files_to_discard = [f for f in file_paths if f not in files_to_unintent]
            if files_to_discard:
                do_discard(files_to_discard)
            return file_paths
        return None


class gs_status_stage_all_files(StatusInterfaceCommand):

    """
    Stage all unstaged files.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        self.add_all_tracked_files()
        self.interface.refresh_repo_status_and_render()


class gs_status_stage_all_files_with_untracked(StatusInterfaceCommand):

    """
    Stage all unstaged files, including new files.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        self.add_all_files()
        self.interface.refresh_repo_status_and_render()


class gs_status_unstage_all_files(StatusInterfaceCommand):

    """
    Unstage all staged changes.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        self.unstage_all_files()
        self.interface.refresh_repo_status_and_render()


class gs_status_discard_all_changes(StatusInterfaceCommand):

    """
    Reset all unstaged files to HEAD.
    """

    @util.actions.destructive(description="discard all unstaged changes, "
                                          "and delete all untracked files")
    def run(self, edit):
        # type: (sublime.Edit) -> None
        self.discard_all_unstaged()
        self.interface.refresh_repo_status_and_render()


class gs_status_ignore_file(StatusInterfaceCommand):

    """
    For each file that is selected or under a cursor, set `--skip-worktree`.
    For each file already skipped, unset `--skip-worktree`.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        file_paths_to_skip = self.get_selected_subjects(
            'staged', 'unstaged', 'untracked', 'added', 'merge-conflicts')
        file_paths_to_restore = self.get_selected_subjects('skipped')
        if file_paths_to_skip:
            self.set_skip_worktree(*file_paths_to_skip)
        if file_paths_to_restore:
            self.unset_skip_worktree(*file_paths_to_restore)

        if file_paths_to_skip or file_paths_to_restore:
            self.window.status_message(
                "Successfully {} `--skip-worktree`.".format(
                    " and ".join(filter_((
                        "set" if file_paths_to_skip else None,
                        "unset" if file_paths_to_restore else None
                    )))
                )
            )
            enqueue_on_worker(self.get_skipped_files)
            self.interface.refresh_repo_status_and_render()


class gs_status_ignore_pattern(StatusInterfaceCommand):

    """
    For the first file that is selected or under a cursor (other
    selections/cursors will be ignored), prompt the user for
    a new pattern to `.gitignore`, prefilled with the filename.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        file_paths = self.get_selected_subjects(
            'staged', 'unstaged', 'untracked', 'added', 'merge-conflicts')
        if file_paths:
            self.window.run_command("gs_ignore_pattern", {"pre_filled": file_paths[0]})


class gs_status_stash(StatusInterfaceCommand):

    """
    Run action from status dashboard to stash commands. Need to have this command to
    read the interface and call the stash commands

    action          multiple stashes
    show            True
    apply           False
    pop             False
    discard         False
    """

    def run(self, edit, action=None):
        # type: (sublime.Edit, str) -> None
        ids = self.get_selected_subjects('stashes')
        if not ids:
            return

        if action == "show":
            self.window.run_command("gs_stash_show", {"stash_ids": ids})
            return

        if len(ids) > 1:
            self.window.status_message("You can only {} one stash at a time.".format(action))
            return

        if action == "apply":
            self.window.run_command("gs_stash_apply", {"stash_id": ids[0]})
        elif action == "pop":
            self.window.run_command("gs_stash_pop", {"stash_id": ids[0]})
        elif action == "drop":
            self.window.run_command("gs_stash_drop", {"stash_id": ids[0]})


class gs_status_launch_merge_tool(StatusInterfaceCommand):

    """
    Launch external merge tool for selected file.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        file_paths = self.get_selected_subjects(
            'staged', 'unstaged', 'untracked', 'added', 'merge-conflicts')
        if len(file_paths) > 1:
            sublime.error_message("You can only launch merge tool for a single file at a time.")
            return

        sublime.set_timeout_async(lambda: self.launch_tool_for_file(file_paths[0]), 0)


class gs_status_use_commit_version(StatusInterfaceCommand):
    # TODO: refactor this alongside interfaces.rebase.GsRebaseUseCommitVersionCommand

    def run(self, edit):
        # type: (sublime.Edit) -> None
        conflicts = self.interface.state['status'].merge_conflicts
        file_paths = self.get_selected_subjects('merge-conflicts')

        for fpath in file_paths:
            if self.is_commit_version_deleted(fpath, conflicts):
                self.git("rm", "--", fpath)
            else:
                self.git("checkout", "--theirs", "--", fpath)
                self.stage_file(fpath)

        self.interface.refresh_repo_status_and_render()

    def is_commit_version_deleted(self, path, conflicts):
        # type: (str, List[FileStatus]) -> bool
        for conflict in conflicts:
            if conflict.path == path:
                return conflict.working_status == "D"
        return False


class gs_status_use_base_version(StatusInterfaceCommand):

    def run(self, edit):
        # type: (sublime.Edit) -> None
        conflicts = self.interface.state['status'].merge_conflicts
        file_paths = self.get_selected_subjects('merge-conflicts')

        for fpath in file_paths:
            if self.is_base_version_deleted(fpath, conflicts):
                self.git("rm", "--", fpath)
            else:
                self.git("checkout", "--ours", "--", fpath)
                self.stage_file(fpath)

        self.interface.refresh_repo_status_and_render()

    def is_base_version_deleted(self, path, conflicts):
        # type: (str, List[FileStatus]) -> bool
        for conflict in conflicts:
            if conflict.path == path:
                return conflict.index_status == "D"
        return False


class gs_status_navigate_file(GsNavigate):

    """
    Move cursor to the next (or previous) selectable item in the dashboard.
    """
    offset = 0

    def get_available_regions(self):
        return self.view.find_by_selector(
            "meta.git-savvy.entity - meta.git-savvy.entity.filename.renamed.to"
            ", meta.git-savvy.summary-header constant.other.git-savvy.sha1"
        )


class gs_status_navigate_goto(GsNavigate):

    """
    Move cursor to the next (or previous) selectable file in the dashboard.
    """
    offset = 0

    def get_available_regions(self):
        return (
            self.view.find_by_selector("gitsavvy.gotosymbol - meta.git-savvy.status.section.skipped")
            + self.view.find_all("Your working directory is clean", sublime.LITERAL)
        )
