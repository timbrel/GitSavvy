from collections import defaultdict
from functools import partial
import threading
import uuid

import sublime
from sublime_plugin import TextCommand, EventListener

from ..git_command import GitCommand, repo_path_for_view_if_cached, repo_path_for_view
from ..settings import GitSavvySettings


if False:
    from typing import Callable, DefaultDict, Dict, Iterable, Optional, TypeVar
    T = TypeVar('T')
    Setter = Callable[[T], None]
    Thunk = Callable[[], None]

    RepoPath = str
    ShortStatus = Optional[str]
    StatusUpdater = Setter[ShortStatus]


_lock = threading.Lock()
filter_ = partial(filter, None)  # type: (Callable[[Iterable[Optional[T]]], Iterable[T]])

active_view = None  # type: Optional[sublime.View]
current_token = {}  # type: Dict[RepoPath, str]
State = defaultdict(dict)  # type: DefaultDict[RepoPath, Dict]


def maybe_update_status_bar(view):
    """Record intent to update the status bar."""
    if view_is_transient(view):
        return

    repo_path = repo_path_for_view(view)
    if repo_path:
        maybe_update_status_async(repo_path, partial(render_status, repo_path))


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


def maybe_update_status_async(repo_path, then):
    # type: (RepoPath, StatusUpdater) -> None
    with _lock:
        current_token[repo_path] = token = uuid.uuid4().hex
    sink = partial(update_status, repo_path, then=then)
    sublime.set_timeout_async(
        partial(executor, lambda: current_token[repo_path] == token, sink)
    )


def update_status(repo_path, then=None):
    # type: (RepoPath, Optional[StatusUpdater]) -> None
    invalidate_token(repo_path)

    git = make_git(repo_path)
    status = fetch_status(git)
    State[repo_path]['short_status'] = status
    if then:
        then(status)


def executor(pred, sink):
    # type: (Thunk, Thunk) -> None
    if pred():
        sink()


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
    # type: (GitCommand) -> Optional[str]
    try:
        return git.get_branch_status_short()
    except Exception:
        return None


def render_status(repo_path, status):
    # type: (RepoPath, ShortStatus) -> None
    for v in active_views():
        if repo_path_for_view(v) == repo_path:
            render(v, status)


def active_views():
    # type: () -> Iterable[sublime.View]
    return filter_(w.active_view() for w in sublime.windows())


def render(view, status):
    # type: (sublime.View, ShortStatus) -> None
    if not GitSavvySettings().get("git_status_in_status_bar"):
        return

    if status:
        view.set_status("gitsavvy-repo-status", status)
    else:
        view.erase_status("gitsavvy-repo-status")


class GsStatusBarEventListener(EventListener):
    on_new_async = staticmethod(maybe_update_status_bar)

    # Sublime calls `on_post_save_async` events only on the primary view.
    # We thus track the state of the `active_view` manually so that we can
    # refresh the status bar of cloned views.

    # Note: We listen for 'on_activated' bc we must draw on the main
    # thread for the happy path to avoid visual yank on the status bar.
    # We also only draw if the repo path is cached bc we must avoid
    # doing expensive work on the main thread.
    def on_activated(self, view):
        global active_view
        active_view = view

        repo_path = repo_path_for_view_if_cached(view)
        if repo_path:
            try:
                status = State[repo_path]['short_status']
            except KeyError:
                ...
            else:
                render(view, status)

        # Defer to the worker for the hard, expensive work!
        sublime.set_timeout_async(partial(maybe_update_status_bar, view))

    def on_post_save_async(self, view):
        global active_view
        if active_view and active_view.buffer_id() == view.buffer_id():
            maybe_update_status_bar(active_view)
        else:
            maybe_update_status_bar(view)


class gs_update_status_bar(TextCommand):
    def run(self, edit):
        sublime.set_timeout_async(partial(maybe_update_status_bar, self.view))
