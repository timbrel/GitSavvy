from functools import partial, wraps
from itertools import chain
import os
import threading

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..git_mixins.status import FileStatus
from ..commands import GsNavigate
from ...common import ui
from ..git_command import GitCommand
from ...common import util

flatten = chain.from_iterable


MYPY = False
if MYPY:
    from typing import Iterable, Iterator, List, Optional, Tuple


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


def distinct_until_state_changed(just_render_fn):
    """Custom `lru_cache`-look-alike to minimize redraws."""
    previous_state = {}

    @wraps(just_render_fn)
    def wrapper(self, *args, **kwargs):
        nonlocal previous_state

        current_state = self.state
        if current_state != previous_state:
            just_render_fn(self, *args, **kwargs)
            previous_state = current_state.copy()

    return wrapper


class GsShowStatusCommand(WindowCommand, GitCommand):

    """
    Open a status view for the active git repository.
    """

    def run(self):
        StatusInterface(repo_path=self.repo_path)


class StatusInterface(ui.Interface, GitCommand):

    """
    Status dashboard.
    """

    interface_type = "status"
    read_only = True
    syntax_file = "Packages/GitSavvy/syntax/status.sublime-syntax"
    word_wrap = False
    tab_size = 2

    template = """\

      BRANCH:  {branch_status}
      ROOT:    {git_root}
      HEAD:    {head}

    {< unstaged_files}
    {< untracked_files}
    {< staged_files}
    {< merge_conflicts}
    {< no_status_message}
    {< stashes}
    {< help}
    """

    template_help = """
      ###################                   ###############
      ## SELECTED FILE ##                   ## ALL FILES ##
      ###################                   ###############

      [o] open file                         [a] stage all unstaged files
      [s] stage file                        [A] stage all unstaged and untracked files
      [u] unstage file                      [U] unstage all staged files
      [d] discard changes to file           [D] discard all unstaged changes
      [h] open file on remote
      [M] launch external merge tool

      [l] diff file inline                  [f] diff all files
      [e] diff file                         [F] diff all cached files

      #############                         #############
      ## ACTIONS ##                         ## STASHES ##
      #############                         #############

      [c] commit                            [t][a] apply stash
      [C] commit, including unstaged        [t][p] pop stash
      [m] amend previous commit             [t][s] show stash
      [p] push current branch               [t][c] create stash
                                            [t][u] create stash including untracked files
      [i] ignore file                       [t][g] create stash of staged changes only
      [I] ignore pattern                    [t][d] drop stash

      [B] abort merge

      ###########
      ## OTHER ##
      ###########

      [g]         show graph repop history
      [?]         toggle this help menu
      [tab]       transition to next dashboard
      [SHIFT-tab] transition to previous dashboard
      [.]         move cursor to next file
      [,]         move cursor to previous file
    {conflicts_bindings}
    -
    """

    conflicts_keybindings = """
    ###############
    ## CONFLICTS ##
    ###############

    [y] use version from your commit
    [b] use version from the base
    """

    template_staged = """
      STAGED:
    {}
    """

    template_unstaged = """
      UNSTAGED:
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

    template_stashes = """
      STASHES:
    {}
    """

    def __init__(self, *args, **kwargs):
        if self._initialized:
            return

        self.conflicts_keybindings = \
            "\n".join(line[2:] for line in self.conflicts_keybindings.split("\n"))
        self._lock = threading.Lock()
        self.state = {
            'staged_files': [],
            'unstaged_files': [],
            'untracked_files': [],
            'merge_conflicts': [],
            'branch_status': '',
            'git_root': '',
            'show_help': True,
            'head': '',
            'stashes': []
        }
        super().__init__(*args, **kwargs)

    def title(self):
        return "STATUS: {}".format(os.path.basename(self.repo_path))

    def refresh_view_state(self):
        """Update all view state.

        Note: For every possible long running process, we enqueue a task
        in a worker thread. We re-render as soon as we receive meaningful
        data which implies that the view is only _eventual_ consistent
        with the real world.
        """
        for thunk in (
            self.fetch_repo_status,
            lambda: {'head': self.get_latest_commit_msg_for_head()},
            lambda: {'stashes': self.get_stashes()},
        ):
            sublime.set_timeout_async(
                partial(self.update_state, thunk, then=self.just_render)
            )

        # These are cheap to compute, so we just do it!
        self.update_state({
            'git_root': self.short_repo_path,
            'show_help': not self.view.settings().get("git_savvy.help_hidden")
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

    def render(self, nuke_cursors=False):
        """Refresh view state and render."""
        self.refresh_view_state()
        self.just_render(nuke_cursors)

        if hasattr(self, "reset_cursor") and nuke_cursors:
            self.reset_cursor()

    @distinct_until_state_changed
    def just_render(self, nuke_cursors=False):
        # TODO: Rewrite to "pureness" so that we don't need a lock here
        # Note: It is forbidden to `update_state` during render, e.g. in
        # any partials.
        with self._lock:
            self.clear_regions()
            rendered = self._render_template()

        self.view.run_command("gs_new_content_and_regions", {
            "content": rendered,
            "regions": self.regions,
            "nuke_cursors": nuke_cursors
        })

        on_special_symbol = any(
            self.view.match_selector(
                s.begin(),
                'meta.git-savvy.section.body.row'
            )
            for s in self.view.sel()
        )
        if not on_special_symbol:
            self.view.run_command("gs_status_navigate_goto")

    def fetch_repo_status(self, delim=None):
        lines = self._get_status()
        files_statuses = self._parse_status_for_file_statuses(lines)
        branch_status = self._get_branch_status_components(lines)

        (staged_files,
         unstaged_files,
         untracked_files,
         merge_conflicts) = self.sort_status_entries(files_statuses)
        branch_status = self._format_branch_status(branch_status, delim="\n           ")

        return {
            'staged_files': staged_files,
            'unstaged_files': unstaged_files,
            'untracked_files': untracked_files,
            'merge_conflicts': merge_conflicts,
            'branch_status': branch_status
        }

    def refresh_repo_status_and_render(self):
        """Refresh `git status` state and render.

        Most actions in the status dashboard only affect the `git status`.
        So instead of calling `render` it is a good optimization to just
        ask this method if appropriate.
        """
        self.update_state(self.fetch_repo_status, self.just_render)

    def after_view_creation(self, view):
        view.settings().set("result_file_regex", EXTRACT_FILENAME_RE)
        view.settings().set("result_base_dir", self.repo_path)

    @ui.partial("branch_status")
    def render_branch_status(self):
        return self.state['branch_status']

    @ui.partial("git_root")
    def render_git_root(self):
        return self.state['git_root']

    @ui.partial("head")
    def render_head(self):
        return self.state['head']

    @ui.partial("staged_files")
    def render_staged_files(self):
        staged_files = self.state['staged_files']
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

    @ui.partial("unstaged_files")
    def render_unstaged_files(self):
        unstaged_files = self.state['unstaged_files']
        if not unstaged_files:
            return ""

        return self.template_unstaged.format("\n".join(
            "  {} {}".format("-" if f.working_status == "D" else " ", f.path)
            for f in unstaged_files
        ))

    @ui.partial("untracked_files")
    def render_untracked_files(self):
        untracked_files = self.state['untracked_files']
        if not untracked_files:
            return ""

        return self.template_untracked.format(
            "\n".join("    " + f.path for f in untracked_files))

    @ui.partial("merge_conflicts")
    def render_merge_conflicts(self):
        merge_conflicts = self.state['merge_conflicts']
        if not merge_conflicts:
            return ""
        return self.template_merge_conflicts.format(
            "\n".join("    " + f.path for f in merge_conflicts))

    @ui.partial("conflicts_bindings")
    def render_conflicts_bindings(self):
        return self.conflicts_keybindings if self.state['merge_conflicts'] else ""

    @ui.partial("no_status_message")
    def render_no_status_message(self):
        return ("\n    Your working directory is clean.\n"
                if not (self.state['staged_files'] or
                        self.state['unstaged_files'] or
                        self.state['untracked_files'] or
                        self.state['merge_conflicts'])
                else "")

    @ui.partial("stashes")
    def render_stashes(self):
        stash_list = self.state['stashes']
        if not stash_list:
            return ""

        return self.template_stashes.format("\n".join(
            "    ({}) {}".format(stash.id, stash.description) for stash in stash_list))

    @ui.partial("help")
    def render_help(self):
        show_help = self.state['show_help']
        if not show_help:
            return ""

        return self.template_help.format(conflicts_bindings=self.render_conflicts_bindings())


ui.register_listeners(StatusInterface)


def get_subjects(view, *sections):
    # type: (sublime.View, str) -> Iterable[sublime.Region]
    return flatten(
        view.find_by_selector(
            'meta.git-savvy.status.section.{} meta.git-savvy.status.subject'.format(section)
        )
        for section in sections
    )


def region_as_tuple(region):
    # type: (sublime.Region) -> Tuple[int, int]
    return region.begin(), region.end()


def region_from_tuple(tuple_):
    # type: (Tuple[int, int]) -> sublime.Region
    return sublime.Region(*tuple_)


def unique_regions(regions):
    # type: (Iterable[sublime.Region]) -> Iterator[sublime.Region]
    # Regions are not hashable so we unpack them to tuples,
    # then use set, finally pack them again
    return map(region_from_tuple, set(map(region_as_tuple, regions)))


def unique_selected_lines(view):
    # type: (sublime.View) -> List[sublime.Region]
    return list(unique_regions(flatten(view.lines(s) for s in view.sel())))


def get_selected_subjects(view, *sections):
    # type: (sublime.View, str) -> List[str]
    selected_lines = unique_selected_lines(view)
    return [
        view.substr(subject)
        for subject in get_subjects(view, *sections)
        if any(line.contains(subject) for line in selected_lines)
    ]


def get_selected_files(view, base_path, *sections):
    # type: (sublime.View, str, str) -> List[str]
    if not sections:
        sections = ('staged', 'unstaged', 'untracked', 'merge-conflicts')

    make_abs_path = partial(os.path.join, base_path)
    return [
        make_abs_path(filename)
        for filename in get_selected_subjects(view, *sections)
    ]


def get_interface(view):
    # type: (sublime.View) -> Optional[StatusInterface]
    interface = ui.get_interface(view.id())
    if not isinstance(interface, StatusInterface):
        return None
    return interface


class GsStatusOpenFileCommand(TextCommand, GitCommand):

    """
    For every file that is selected or under a cursor, open a that
    file in a new view.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        window = self.view.window()
        if not window:
            return

        for fpath in get_selected_files(self.view, self.repo_path):
            window.open_file(fpath)


class GsStatusOpenFileOnRemoteCommand(TextCommand, GitCommand):

    """
    For every file that is selected or under a cursor, open a new browser
    window to that file on GitHub.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        file_paths = get_selected_subjects(self.view, 'staged', 'unstaged', 'merge-conflicts')
        if file_paths:
            self.view.run_command("gs_github_open_file_on_remote", {"fpath": file_paths})


class GsStatusDiffInlineCommand(TextCommand, GitCommand):

    """
    For every file selected or under a cursor, open a new inline-diff view for
    that file.  If the file is staged, open the inline-diff in cached mode.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        window = self.view.window()
        if not window:
            return

        repo_path = self.repo_path
        non_cached_files = get_selected_files(self.view, repo_path, 'unstaged', 'merge-conflicts')
        cached_files = get_selected_files(self.view, repo_path, 'staged')

        sublime.set_timeout_async(
            lambda: self.load_inline_diff_views(window, non_cached_files, cached_files)
        )

    def load_inline_diff_views(self, window, non_cached_files, cached_files):
        # type: (sublime.Window, List[str], List[str]) -> None
        for fpath in non_cached_files:
            syntax = util.file.get_syntax_for_file(fpath)
            settings = {
                "file_path": fpath,
                "repo_path": self.repo_path,
                "syntax": syntax
            }
            window.run_command("gs_inline_diff", {"settings": settings})

        for fpath in cached_files:
            syntax = util.file.get_syntax_for_file(fpath)
            settings = {
                "file_path": fpath,
                "repo_path": self.repo_path,
                "syntax": syntax
            }
            window.run_command("gs_inline_diff", {
                "settings": settings,
                "cached": True
            })


class GsStatusDiffCommand(TextCommand, GitCommand):

    """
    For every file selected or under a cursor, open a new diff view for
    that file.  If the file is staged, open the diff in cached mode.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        window = self.view.window()
        if not window:
            return

        repo_path = self.repo_path
        non_cached_files = get_selected_files(
            self.view, repo_path, 'unstaged', 'untracked', 'merge-conflicts'
        )
        cached_files = get_selected_files(self.view, repo_path, 'staged')

        sublime.set_timeout_async(
            lambda: self.load_diff_windows(
                window,  # type: ignore  # https://github.com/python/mypy/issues/4297
                non_cached_files,
                cached_files
            )
        )

    def load_diff_windows(self, window, non_cached_files, cached_files):
        # type: (sublime.Window, List[str], List[str]) -> None
        for fpath in non_cached_files:
            window.run_command("gs_diff", {
                "file_path": fpath,
                "in_cached_mode": False,
            })

        for fpath in cached_files:
            window.run_command("gs_diff", {
                "file_path": fpath,
                "in_cached_mode": True,
            })


class GsStatusStageFileCommand(TextCommand, GitCommand):

    """
    For every file that is selected or under a cursor, if that file is
    unstaged, stage it.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        window, interface = self.view.window(), get_interface(self.view)
        if not (window and interface):
            return

        file_paths = get_selected_subjects(self.view, 'unstaged', 'untracked', 'merge-conflicts')
        if file_paths:
            self.stage_file(*file_paths, force=False)
            window.status_message("Staged files successfully.")
            interface.refresh_repo_status_and_render()


class GsStatusUnstageFileCommand(TextCommand, GitCommand):

    """
    For every file that is selected or under a cursor, if that file is
    staged, unstage it.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        window, interface = self.view.window(), get_interface(self.view)
        if not (window and interface):
            return

        file_paths = get_selected_subjects(self.view, 'staged', 'merge-conflicts')
        if file_paths:
            self.unstage_file(*file_paths)
            window.status_message("Unstaged files successfully.")
            interface.refresh_repo_status_and_render()


class GsStatusDiscardChangesToFileCommand(TextCommand, GitCommand):

    """
    For every file that is selected or under a cursor, if that file is
    unstaged, reset the file to HEAD.  If it is untracked, delete it.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        window, interface = self.view.window(), get_interface(self.view)
        if not (window and interface):
            return
        untracked_files = self.discard_untracked()
        unstaged_files = self.discard_unstaged()
        if untracked_files or unstaged_files:
            window.status_message("Successfully discarded changes.")
            interface.refresh_repo_status_and_render()

    def discard_untracked(self):
        # type: () -> Optional[List[str]]
        file_paths = get_selected_subjects(self.view, 'untracked')

        @util.actions.destructive(description="discard one or more untracked files")
        def do_discard():
            self.discard_untracked_file(*file_paths)
            return file_paths

        if file_paths:
            return do_discard()
        return None

    def discard_unstaged(self):
        # type: () -> Optional[List[str]]
        file_paths = get_selected_subjects(self.view, 'unstaged', 'merge-conflicts')

        @util.actions.destructive(description="discard one or more unstaged files")
        def do_discard():
            self.checkout_file(*file_paths)
            return file_paths

        if file_paths:
            return do_discard()
        return None


class GsStatusStageAllFilesCommand(TextCommand, GitCommand):

    """
    Stage all unstaged files.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        interface = get_interface(self.view)
        if not interface:
            return

        self.add_all_tracked_files()
        interface.refresh_repo_status_and_render()


class GsStatusStageAllFilesWithUntrackedCommand(TextCommand, GitCommand):

    """
    Stage all unstaged files, including new files.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        interface = get_interface(self.view)
        if not interface:
            return

        self.add_all_files()
        interface.refresh_repo_status_and_render()


class GsStatusUnstageAllFilesCommand(TextCommand, GitCommand):

    """
    Unstage all staged changes.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        interface = get_interface(self.view)
        if not interface:
            return

        self.unstage_all_files()
        interface.refresh_repo_status_and_render()


class GsStatusDiscardAllChangesCommand(TextCommand, GitCommand):

    """
    Reset all unstaged files to HEAD.
    """

    @util.actions.destructive(description="discard all unstaged changes, "
                                          "and delete all untracked files")
    def run(self, edit):
        # type: (sublime.Edit) -> None
        interface = get_interface(self.view)
        if not interface:
            return

        self.discard_all_unstaged()
        interface.refresh_repo_status_and_render()


class GsStatusIgnoreFileCommand(TextCommand, GitCommand):

    """
    For each file that is selected or under a cursor, add an
    entry to the git root's `.gitignore` file.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        window, interface = self.view.window(), get_interface(self.view)
        if not (window and interface):
            return

        file_paths = get_selected_subjects(
            self.view, 'staged', 'unstaged', 'untracked', 'merge-conflicts'
        )
        if file_paths:
            for fpath in file_paths:
                self.add_ignore(os.path.join("/", fpath))
            window.status_message("Successfully ignored files.")
            interface.refresh_repo_status_and_render()


class GsStatusIgnorePatternCommand(TextCommand, GitCommand):

    """
    For the first file that is selected or under a cursor (other
    selections/cursors will be ignored), prompt the user for
    a new pattern to `.gitignore`, prefilled with the filename.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        window, interface = self.view.window(), get_interface(self.view)
        if not (window and interface):
            return

        file_paths = get_selected_subjects(
            self.view, 'staged', 'unstaged', 'untracked', 'merge-conflicts'
        )
        if file_paths:
            window.run_command("gs_ignore_pattern", {"pre_filled": file_paths[0]})


class GsStatusStashCommand(TextCommand, GitCommand):

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
        window = self.view.window()
        if not window:
            return

        ids = get_selected_subjects(self.view, 'stashes')
        if not ids:
            return

        if action == "show":
            window.run_command("gs_stash_show", {"stash_ids": ids})
            return

        if len(ids) > 1:
            window.status_message("You can only {} one stash at a time.".format(action))
            return

        if action == "apply":
            window.run_command("gs_stash_apply", {"stash_id": ids[0]})
        elif action == "pop":
            window.run_command("gs_stash_pop", {"stash_id": ids[0]})
        elif action == "drop":
            window.run_command("gs_stash_drop", {"stash_id": ids[0]})


class GsStatusLaunchMergeToolCommand(TextCommand, GitCommand):

    """
    Launch external merge tool for selected file.
    """

    def run(self, edit):
        # type: (sublime.Edit) -> None
        file_paths = get_selected_subjects(
            self.view, 'staged', 'unstaged', 'untracked', 'merge-conflicts'
        )
        if len(file_paths) > 1:
            sublime.error_message("You can only launch merge tool for a single file at a time.")
            return

        sublime.set_timeout_async(lambda: self.launch_tool_for_file(file_paths[0]), 0)


class GsStatusUseCommitVersionCommand(TextCommand, GitCommand):
    # TODO: refactor this alongside interfaces.rebase.GsRebaseUseCommitVersionCommand

    def run(self, edit):
        # type: (sublime.Edit) -> None
        interface = get_interface(self.view)
        if not interface:
            return

        conflicts = interface.state['merge_conflicts']
        file_paths = get_selected_subjects(self.view, 'merge-conflicts')

        for fpath in file_paths:
            if self.is_commit_version_deleted(fpath, conflicts):
                self.git("rm", "--", fpath)
            else:
                self.git("checkout", "--theirs", "--", fpath)
                self.stage_file(fpath)

        interface.refresh_repo_status_and_render()

    def is_commit_version_deleted(self, path, conflicts):
        # type: (str, List[FileStatus]) -> bool
        for conflict in conflicts:
            if conflict.path == path:
                return conflict.working_status == "D"
        return False


class GsStatusUseBaseVersionCommand(TextCommand, GitCommand):

    def run(self, edit):
        # type: (sublime.Edit) -> None
        interface = get_interface(self.view)
        if not interface:
            return

        conflicts = interface.state['merge_conflicts']
        file_paths = get_selected_subjects(self.view, 'merge-conflicts')

        for fpath in file_paths:
            if self.is_base_version_deleted(fpath, conflicts):
                self.git("rm", "--", fpath)
            else:
                self.git("checkout", "--ours", "--", fpath)
                self.stage_file(fpath)

        interface.refresh_repo_status_and_render()

    def is_base_version_deleted(self, path, conflicts):
        # type: (str, List[FileStatus]) -> bool
        for conflict in conflicts:
            if conflict.path == path:
                return conflict.index_status == "D"
        return False


class GsStatusNavigateFileCommand(GsNavigate):

    """
    Move cursor to the next (or previous) selectable item in the dashboard.
    """
    offset = 0

    def get_available_regions(self):
        return self.view.find_by_selector(
            "meta.git-savvy.entity - meta.git-savvy.entity.filename.renamed.to"
        )


class GsStatusNavigateGotoCommand(GsNavigate):

    """
    Move cursor to the next (or previous) selectable file in the dashboard.
    """
    offset = 0

    def get_available_regions(self):
        return (
            self.view.find_by_selector("gitsavvy.gotosymbol")
            + self.view.find_all("Your working directory is clean", sublime.LITERAL)
        )
