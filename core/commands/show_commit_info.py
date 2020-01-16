from contextlib import contextmanager
from functools import lru_cache

import sublime
from sublime_plugin import WindowCommand

from . import intra_line_colorizer
from ..git_command import GitCommand


MYPY = False
if MYPY:
    from typing import Dict, Optional, Tuple


storage = {}  # type: Dict[str, Tuple[float, float]]
PANEL_NAME = "show_commit_info"


def ensure_panel(window, name, syntax=None):
    # type: (sublime.Window, str, str) -> sublime.View
    output_view = window.find_output_panel(name)
    if output_view:
        return output_view

    output_view = window.create_output_panel(name)
    output_view.set_read_only(True)
    if syntax:
        output_view.set_syntax_file(syntax)
    return output_view


def panel_is_visible(window, name):
    # type: (sublime.Window, str) -> bool
    return window.active_panel() == "output.{}".format(name)


def ensure_panel_is_visible(window, name):
    # type: (sublime.Window, str) -> None
    if not panel_is_visible(window, name):
        window.run_command("show_panel", {"panel": "output.{}".format(name)})


class GsShowCommitInfoCommand(WindowCommand, GitCommand):
    def run(self, commit_hash, file_path=None):
        self._commit_hash = commit_hash
        self._file_path = file_path
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        output_view = ensure_panel(
            self.window, PANEL_NAME, syntax="Packages/GitSavvy/syntax/show_commit.sublime-syntax"
        )

        if self._commit_hash:
            show_full = self.savvy_settings.get("show_full_commit_info")
            show_diffstat = self.savvy_settings.get("show_diffstat")
            text = self.show_commit(self._commit_hash, self._file_path, show_diffstat, show_full)
        else:
            text = None

        sublime.set_timeout(lambda: _draw(self.window, output_view, text, self._commit_hash))

    @lru_cache(maxsize=64)
    def show_commit(self, commit_hash, file_path, show_diffstat, show_full):
        return self.git(
            "show",
            "--no-color",
            "--format=fuller",
            "--stat" if show_diffstat else None,
            "--patch" if show_full else None,
            commit_hash,
            "--" if file_path else None,
            file_path if file_path else None
        )


def _draw(window, view, text, commit):
    # type: (sublime.Window, sublime.View, Optional[str], str) -> None
    if text is not None:
        with restore_viewport_position(view, commit):
            view.run_command("gs_replace_view_text", {"text": text, "nuke_cursors": True})

        intra_line_colorizer.annotate_intra_line_differences(view)

    # In case we reuse a hidden panel, show the panel after updating
    # the content to reduce visual flicker.
    ensure_panel_is_visible(window, PANEL_NAME)


@contextmanager
def restore_viewport_position(view, next_commit):
    prev_commit = view.settings().get("git_savvy.show_commit_info.commit")
    if prev_commit:
        storage[prev_commit] = view.viewport_position()

    yield

    view.settings().set("git_savvy.show_commit_info.commit", next_commit)
    prev_position = storage.get(next_commit, (0, 0))
    view.set_viewport_position(prev_position, False)
