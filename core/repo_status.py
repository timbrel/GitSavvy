from functools import partial
import re
import string
import threading
import uuid


import sublime
from sublime_plugin import TextCommand, EventListener

from .git_command import GitCommand, repo_path_for_view
from .git_mixins.status import FileStatus, MERGE_CONFLICT_PORCELAIN_STATUSES
from . import state


if False:
    from typing import Callable, Dict, Iterable, List, Optional, TypeVar
    from .state import RepoPath, RepoStatus

    T = TypeVar('T')
    Thunk = Callable[[], T]


_lock = threading.Lock()
filter_ = partial(filter, None)  # type: (Callable[[Iterable[Optional[T]]], Iterable[T]])

active_view = None  # type: Optional[sublime.View]
current_token = {}  # type: Dict[RepoPath, str]


class GsRegularStatusUpdaterEventListener(EventListener):
    def on_new_async(self, view):
        maybe_update_status_for_view(view)

    # Sublime calls `on_post_save_async` events only on the primary view.
    # We thus track the state of the `active_view` manually so that we can
    # refresh the status bar of cloned views.

    def on_activated_async(self, view):
        global active_view
        active_view = view

        maybe_update_status_for_view(view)

    def on_post_save_async(self, view):
        global active_view
        if active_view and active_view.buffer_id() == view.buffer_id():
            maybe_update_status_for_view(active_view)
        else:
            maybe_update_status_for_view(view)


class gs_update_status_bar(TextCommand):
    def run(self, edit):
        sublime.set_timeout_async(partial(maybe_update_status_for_view, self.view))


def maybe_update_status_for_view(view):
    """Record intent to update the status bar."""
    if view_is_transient(view):
        return

    repo_path = repo_path_for_view(view)
    if repo_path:
        maybe_update_status_async(repo_path)


def view_is_transient(view: sublime.View) -> bool:
    """Return whether a view can be considered 'transient'.

    For our purpose, transient views are 'detached' views or widgets
    (aka quick or input panels).
    """

    # 'Detached' (already closed) views don't have a window.
    window = view.window()
    if not window:
        return True

    # Widgets are normal views but the typical getters don't list them.
    group, index = window.get_view_index(view)
    if group == -1:
        return True

    return False


def maybe_update_status_async(repo_path):
    # type: (RepoPath) -> None
    with _lock:
        current_token[repo_path] = token = uuid.uuid4().hex
    prop = lambda: current_token.get(repo_path) == token  # type: Thunk[bool]
    sink = partial(update_status, repo_path)  # type: Thunk
    sublime.set_timeout_async(
        partial(maybe_execute, prop, sink)
    )


def maybe_execute(prop, sink):
    # type: (Thunk[bool], Thunk) -> None
    if prop():
        sink()


def update_status(repo_path):
    # type: (RepoPath) -> None
    invalidate_token(repo_path)

    git = make_git(repo_path)
    status = fetch_status(git)
    state.update_state(repo_path, status)


def invalidate_token(repo_path):
    # type: (RepoPath) -> None
    with _lock:
        current_token.pop(repo_path, None)


def make_git(repo_path):
    # type: (RepoPath) -> GitCommand
    git = GitCommand()
    git.repo_path = repo_path
    return git


def fetch_status(git):
    # type: (GitCommand) -> RepoStatus
    first_line, *rest = git._get_status()
    rebasing = git.in_rebase()
    rebase_branch_name = git.rebase_branch_name() if rebasing else None
    merging = git.in_merge()
    merge_head = git.merge_head() if merging else None

    info = {
        'clean': len(rest) == 0,
        'rebasing': rebasing,
        'rebase_branch_name': rebase_branch_name,
        'merging': merging,
        'merge_head': merge_head
    }  # type: RepoStatus

    info.update(parse_first_line(first_line))
    file_statuses = info['file_statuses'] = parse_file_statuses(rest)
    info.update(group_status_entries(file_statuses))

    info['short_status'] = format_short_status(info)
    info['branch_status'] = format_long_status(info)

    return info


def format_short_status(info):
    # type: (RepoStatus) -> str
    if info['rebasing']:
        return "(no branch, rebasing {})".format(info['rebase_branch_name'])

    dirty = "" if info['clean'] else "*"

    if info['detached']:
        return "DETACHED" + dirty

    assert info['branch']
    output = info['branch'] + dirty  # type: ignore

    if info['ahead']:
        output += "+{}".format(info['ahead'])
    if info['behind']:
        output += "-{}".format(info['behind'])

    merge_head = info['merge_head'] if info['merging'] else ""
    return output if not merge_head else output + " (merging {})".format(merge_head)


def format_long_status(info):
    # type: (RepoStatus) -> str
    delim = "\n           "
    secondary = []

    if info['detached']:
        status = "HEAD is in a detached state."

    else:
        tracking = " tracking `{}`".format(info['remote'])
        status = "On branch `{}`{}.".format(info['branch'], tracking if info['remote'] else "")

        if info['ahead'] and info['behind']:
            secondary.append("You're ahead by {} and behind by {}.".format(info['ahead'], info['behind']))
        elif info['ahead']:
            secondary.append("You're ahead by {}.".format(info['ahead']))
        elif info['behind']:
            secondary.append("You're behind by {}.".format(info['behind']))
        elif info['gone']:
            secondary.append("The remote branch is gone.")

    if info['merging']:
        secondary.append("Merging {}.".format(info['merge_head']))

    if info['rebasing']:
        secondary.append("Rebasing {}.".format(info['rebase_branch_name']))

    return delim.join([status] + secondary) if secondary else status


def parse_first_line(first_line):
    # type: (str) -> RepoStatus
    if first_line.startswith("## HEAD (no branch)"):
        return {
            'detached': True,
            'gone': False
        }

    if (
        first_line.startswith("## No commits yet on ")
        # older git used these
        or first_line.startswith("## Initial commit on ")
    ):
        first_line = first_line[:3] + first_line[21:]

    valid_punctuation = "".join(c for c in string.punctuation if c not in "~^:?*[\\")
    branch_pattern = "[A-Za-z0-9" + re.escape(valid_punctuation) + "\u263a-\U0001f645]+?"
    branch_suffix = r"( \[((ahead (\d+))(, )?)?(behind (\d+))?(gone)?\])?)"
    short_status_pattern = "## (" + branch_pattern + r")(\.\.\.(" + branch_pattern + ")" + branch_suffix + "?$"
    status_match = re.match(short_status_pattern, first_line)
    assert status_match
    branch, _, remote, _, _, _, ahead, _, _, behind, gone = status_match.groups()

    return {
        'detached': False,
        'branch': branch,
        'remote': remote,
        'ahead': int(ahead or 0),
        'behind': int(behind or 0),
        'gone': bool(gone)
    }


def parse_file_statuses(lines):
    # type: (List[str]) -> List[FileStatus]
    porcelain_entries = lines.__iter__()
    entries = []

    for entry in porcelain_entries:
        if not entry:
            continue
        index_status = entry[0]
        working_status = entry[1].strip() or None
        path = entry[3:]
        path_alt = porcelain_entries.__next__() if index_status in ["R", "C"] else None
        entries.append(FileStatus(path, path_alt, index_status, working_status))

    return entries


def group_status_entries(file_status_list):
    # type: (List[FileStatus]) -> RepoStatus
    staged, unstaged, untracked, conflicts = [], [], [], []

    for f in file_status_list:
        if (f.index_status, f.working_status) in MERGE_CONFLICT_PORCELAIN_STATUSES:
            conflicts.append(f)
            continue
        if f.index_status == "?":
            untracked.append(f)
            continue
        elif f.working_status in ("M", "D", "T"):
            unstaged.append(f)
        if f.index_status != " ":
            staged.append(f)

    return {
        'staged_files': staged,
        'unstaged_files': unstaged,
        'untracked_files': untracked,
        'merge_conflicts': conflicts
    }