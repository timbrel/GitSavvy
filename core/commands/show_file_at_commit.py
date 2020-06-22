import os

import sublime
from sublime_plugin import TextCommand, WindowCommand

from ..git_command import GitCommand
from ..runtime import enqueue_on_worker, text_command
from ..utils import flash
from ..view import replace_view_content
from ...common import util
from .log import LogMixin


__all__ = (
    "gs_show_file_at_commit",
    "gs_show_file_at_commit_refresh",
    "gs_show_current_file_at_commit",
    "gs_show_current_file",
    "gs_show_file_at_commit_open_previous_commit",
    "gs_show_file_at_commit_open_next_commit",
    "gs_show_file_at_commit_open_commit",
    "gs_show_file_at_commit_open_file_on_working_dir",
    "gs_show_file_at_commit_open_graph_context",
)

MYPY = False
if MYPY:
    from typing import Optional


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
    def run(self, edit, line, col=None):
        # type: (...) -> None
        view = self.view
        settings = view.settings()
        file_path = settings.get("git_savvy.file_path")
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")

        text = self.get_file_content_at_commit(file_path, commit_hash)
        render(view, text, line, col)
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
def render(view, text, line, col):
    # type: (sublime.View, str, Optional[int], Optional[int]) -> None
    replace_view_content(view, text)
    if line is not None:
        move_cursor_to_line_col(view, line, col)


def move_cursor_to_line_col(view, line, col):
    # type: (sublime.View, int, Optional[int]) -> None
    # Herein: Line numbers are one-based, rows are zero-based.
    if col is None:
        col = 1
    pt = view.text_point(max(0, line - 1), max(0, col - 1))
    view.sel().clear()
    view.sel().add(sublime.Region(pt))
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

        settings.set("git_savvy.show_file_at_commit_view.commit", previous_commit)
        view.run_command("gs_show_file_at_commit_refresh", {
            "line": None,
            "col": None
        })
        flash(view, "On commit {}".format(previous_commit))


class gs_show_file_at_commit_open_next_commit(TextCommand, GitCommand):
    def run(self, edit):
        # type: (...) -> None
        view = self.view

        settings = view.settings()
        file_path = settings.get("git_savvy.file_path")
        commit_hash = settings.get("git_savvy.show_file_at_commit_view.commit")

        next_commit = self.next_commit(commit_hash, file_path)
        if not next_commit:
            flash(view, "No newer commit found.")
            return

        settings.set("git_savvy.show_file_at_commit_view.commit", next_commit)
        view.run_command("gs_show_file_at_commit_refresh", {
            "line": None,
            "col": None
        })
        flash(view, "On commit {}".format(next_commit))


class gs_show_current_file_at_commit(gs_show_file_at_commit):

    @util.view.single_cursor_coords
    def run(self, coords, commit_hash, lineno=None, lang=None):
        if not lang:
            lang = self.window.active_view().settings().get('syntax')
        if lineno is None:
            lineno = self.find_matching_lineno(None, commit_hash, coords[0] + 1)
        super().run(
            commit_hash=commit_hash,
            filepath=self.file_path,
            lineno=lineno,
            lang=lang)


class gs_show_current_file(LogMixin, WindowCommand, GitCommand):
    """
    Show a panel of commits of current file on current branch and
    then open the file at the selected commit.
    """

    def run(self):
        super().run(file_path=self.file_path)

    def do_action(self, commit_hash, **kwargs):
        self.window.run_command("gs_show_current_file_at_commit", {
            "commit_hash": commit_hash
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
