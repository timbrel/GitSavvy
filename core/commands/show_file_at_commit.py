import os

import sublime
from sublime_plugin import TextCommand, WindowCommand

from ..git_command import GitCommand
from ..runtime import enqueue_on_worker, text_command
from ..utils import flash
from ..view import capture_cur_position, replace_view_content
from ...common import util
from .log import LogMixin


__all__ = (
    "gs_show_file_at_commit",
    "gs_show_file_at_commit_refresh",
    "gs_show_current_file",
    "gs_show_file_at_commit_open_previous_commit",
    "gs_show_file_at_commit_open_next_commit",
    "gs_show_file_at_commit_open_commit",
    "gs_show_file_at_commit_open_file_on_working_dir",
    "gs_show_file_at_commit_open_graph_context",
)

MYPY = False
if MYPY:
    from typing import Dict, Optional


SHOW_COMMIT_TITLE = "FILE: {} --{}"


class gs_show_file_at_commit(WindowCommand, GitCommand):

    def run(self, commit_hash, filepath, check_for_renames=False, lineno=1, col=1, lang=None):
        enqueue_on_worker(
            self.run_impl,
            commit_hash,
            filepath,
            check_for_renames,
            lineno,
            col,
            lang,
        )

    def run_impl(self, commit_hash, file_path, check_for_renames, lineno, col, lang):
        # need to get repo_path before the new view is created.
        repo_path = self.repo_path
        view = util.view.get_scratch_view(self, "show_file_at_commit")
        settings = view.settings()
        settings.set("git_savvy.show_file_at_commit_view.commit", commit_hash)
        settings.set("git_savvy.file_path", file_path)
        settings.set("git_savvy.repo_path", repo_path)
        if not lang:
            lang = util.file.guess_syntax_for_file(self.window, file_path)
        title = SHOW_COMMIT_TITLE.format(
            os.path.basename(file_path),
            self.get_short_hash(commit_hash),
        )

        view.set_syntax_file(lang)
        view.set_name(title)

        if check_for_renames:
            file_path = self.filename_at_commit(file_path, commit_hash)

        view.run_command("gs_show_file_at_commit_refresh", {
            "line": lineno,
            "col": col
        })


class gs_show_file_at_commit_refresh(TextCommand, GitCommand):
    def run(self, edit, line=None, col=None, row_offset=None):
        # type: (sublime.Edit, int, int, float) -> None
        view = self.view
        settings = view.settings()
        file_path = settings.get("git_savvy.file_path")
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")

        text = self.get_file_content_at_commit(file_path, commit_hash)
        render(view, text, line, col, row_offset)
        view.reset_reference_document()
        self.update_title(commit_hash, file_path)

        enqueue_on_worker(self.update_reference_document, commit_hash, file_path)

    def update_reference_document(self, commit_hash, file_path):
        self.view.set_reference_document(self.previous_file_version(commit_hash, file_path))

    def update_title(self, commit_hash, file_path):
        title = SHOW_COMMIT_TITLE.format(
            os.path.basename(file_path),
            self.get_short_hash(commit_hash),
        )
        self.view.set_name(title)

    def previous_file_version(self, current_commit, file_path):
        # type: (str, str) -> str
        previous_commit = self.previous_commit(current_commit, file_path)
        return self.get_file_content_at_commit(file_path, previous_commit)


@text_command
def render(view, text, line, col, row_offset=None):
    # type: (sublime.View, str, Optional[int], Optional[int], float) -> None
    replace_view_content(view, text)
    if line is not None:
        move_cursor_to_line_col(view, line, col, row_offset)


def move_cursor_to_line_col(view, line, col, row_offset=None):
    # type: (sublime.View, int, Optional[int], Optional[float]) -> None
    # Herein: Line numbers are one-based, rows are zero-based.
    if col is None:
        col = 1
    row, col = line - 1, col - 1
    pt = view.text_point(row, col)
    view.sel().clear()
    view.sel().add(sublime.Region(pt))

    if row_offset is not None:
        vy = (row - row_offset) * view.line_height()
        vx, _ = view.viewport_position()
        view.set_viewport_position((vx, vy))
    else:
        view.show(pt)


class gs_show_file_at_commit_open_previous_commit(TextCommand, GitCommand):
    def run(self, edit):
        # type: (...) -> None
        view = self.view

        settings = view.settings()
        file_path = settings.get("git_savvy.file_path")
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")

        previous_commit = self.previous_commit(commit_hash, file_path)
        if not previous_commit:
            flash(view, "No older commit found.")
            return

        remember_next_commit_for(view, {previous_commit: commit_hash})
        settings.set("git_savvy.show_file_at_commit_view.commit", previous_commit)

        pos = capture_cur_position(view)
        if pos is None:
            line, col, offset = None, None, None
        else:
            row, col, offset = pos
            line = self.find_matching_lineno(commit_hash, previous_commit, row + 1, file_path)
            col += 1
        view.run_command("gs_show_file_at_commit_refresh", {
            "line": line,
            "col": col,
            "row_offset": offset
        })
        flash(view, "On commit {}".format(previous_commit))


class gs_show_file_at_commit_open_next_commit(TextCommand, GitCommand):
    def run(self, edit):
        # type: (...) -> None
        view = self.view

        settings = view.settings()
        file_path = settings.get("git_savvy.file_path")
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")

        next_commit = (
            recall_next_commit_for(view, commit_hash)
            or self.next_commit(commit_hash, file_path)
        )
        if not next_commit:
            flash(view, "No newer commit found.")
            return

        settings.set("git_savvy.show_file_at_commit_view.commit", next_commit)
        pos = capture_cur_position(view)
        if pos is None:
            line, col, offset = None, None, None
        else:
            row, col, offset = pos
            line = self.reverse_find_matching_lineno(next_commit, commit_hash, row + 1, file_path)
            col += 1
        view.run_command("gs_show_file_at_commit_refresh", {
            "line": line,
            "col": col,
            "row_offset": offset
        })
        flash(view, "On commit {}".format(next_commit))


def remember_next_commit_for(view, mapping):
    # type: (sublime.View, Dict[str, str]) -> None
    settings = view.settings()
    store = settings.get("git_savvy.show_file_at_commit.next_commits", {})  # type: Dict[str, str]
    store.update(mapping)
    settings.set("git_savvy.show_file_at_commit.next_commits", store)


def recall_next_commit_for(view, commit_hash):
    # type: (sublime.View, str) -> Optional[str]
    settings = view.settings()
    store = settings.get("git_savvy.show_file_at_commit.next_commits", {})  # type: Dict[str, str]
    return store.get(commit_hash)


class gs_show_current_file(LogMixin, WindowCommand, GitCommand):
    """
    Show a panel of commits of current file on current branch and
    then open the file at the selected commit.
    """

    def run(self):
        super().run(file_path=self.file_path)

    def do_action(self, commit_hash, **kwargs):
        view = self.window.active_view()
        if not view:
            print("RuntimeError: Window has no active view")
            return

        pos = capture_cur_position(view)
        if pos is None:
            lineno, col, offset = None, None, None
        else:
            row, col, offset = pos
            lineno = self.find_matching_lineno(None, commit_hash, row + 1)
            col += 1

        self.window.run_command("gs_show_file_at_commit", {
            "commit_hash": commit_hash,
            "filepath": self.file_path,
            "lineno": lineno,
            "col": col,
            "lang": view.settings().get('syntax')
        })


class gs_show_file_at_commit_open_commit(TextCommand):
    def run(self, edit):
        # type: (...) -> None
        window = self.view.window()
        if not window:
            return

        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")
        assert commit_hash

        window.run_command("gs_show_commit", {"commit_hash": commit_hash})


class gs_show_file_at_commit_open_file_on_working_dir(TextCommand, GitCommand):
    def run(self, edit):
        # type: (...) -> None
        window = self.view.window()
        if not window:
            return

        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")
        file_path = settings.get("git_savvy.file_path")
        assert commit_hash
        assert file_path

        full_path = os.path.join(self.repo_path, file_path)
        row, col = self.view.rowcol(self.view.sel()[0].begin())
        row = self.find_matching_lineno(commit_hash, None, row + 1, full_path)
        window.open_file(
            "{file}:{row}:{col}".format(file=full_path, row=row, col=col),
            sublime.ENCODED_POSITION
        )


class gs_show_file_at_commit_open_graph_context(TextCommand, GitCommand):
    def run(self, edit):
        # type: (...) -> None
        window = self.view.window()
        if not window:
            return

        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")
        assert commit_hash

        window.run_command("gs_graph", {
            "all": True,
            "follow": self.get_short_hash(commit_hash),
        })
