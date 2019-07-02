from functools import partial
import threading
import uuid

import sublime
from sublime_plugin import TextCommand, EventListener

from ..git_command import GitCommand, repo_path_for_view


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


if False:
    from typing import Callable, Dict, Optional


current_token = {}  # type: Dict[sublime.ViewId, str]
_lock = threading.Lock()
active_view = None  # type: Optional[sublime.View]


def maybe_update_status_bar(view):
    # type: (sublime.View) -> None
    if view_is_transient(view):
        return

    vid = view.id()
    with _lock:
        current_token[vid] = token = uuid.uuid4().hex
    sink = partial(update_status_bar, view)
    sublime.set_timeout_async(partial(executor, sink, vid, token))


def update_status_bar(view):
    # type: (sublime.View) -> None
    invalidate_token(view)
    repo_path = repo_path_for_view(view)
    if not repo_path:
        return

    git = make_git(repo_path)
    render(view, fetch_status(git))


def executor(sink, vid, token):
    # type: (Callable[[], None], sublime.ViewId, str) -> None
    if current_token.get(vid) == token:
        sink()


def invalidate_token(view):
    # type: (sublime.View) -> None
    with _lock:
        current_token.pop(view.id(), None)


def make_git(repo_path):
    # type: (str) -> GitCommand
    git = GitCommand()
    git.repo_path = repo_path
    return git


def fetch_status(git):
    # type: (GitCommand) -> Optional[str]
    try:
        return git.get_branch_status_short()
    except Exception:
        return None


def render(view, status):
    # type: (sublime.View, Optional[str]) -> None
    if status:
        view.set_status("gitsavvy-repo-status", status)
    else:
        view.erase_status("gitsavvy-repo-status")


class GsStatusBarEventListener(EventListener):
    on_new_async = staticmethod(maybe_update_status_bar)

    # Sublime calls `on_post_save_async` events only on the primary view.
    # We thus track the state of the `active_view` manually so that we can
    # refresh the status bar of cloned views.

    def on_activated_async(self, view):
        global active_view
        active_view = view

        maybe_update_status_bar(view)

    def on_post_save_async(self, view):
        global active_view
        if active_view and active_view.buffer_id() == view.buffer_id():
            maybe_update_status_bar(active_view)
        else:
            maybe_update_status_bar(view)


class GsUpdateStatusBarCommand(TextCommand, GitCommand):
    """Record intent to update the status bar."""
    def run(self, edit):
        sublime.set_timeout_async(partial(maybe_update_status_bar, self.view))
