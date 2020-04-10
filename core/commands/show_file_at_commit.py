import os

import sublime
from sublime_plugin import TextCommand, WindowCommand

from ..git_command import GitCommand
from ..runtime import enqueue_on_ui, enqueue_on_worker
from ..view import replace_view_content
from ...common import util
from .log import LogMixin


__all__ = (
    "gs_show_file_at_commit",
    "gs_show_current_file_at_commit",
    "gs_show_current_file",
    "gs_show_file_at_commit_open_commit",
    "gs_show_file_at_commit_open_file_on_working_dir",
    "gs_show_file_at_commit_open_graph_context",
)

SHOW_COMMIT_TITLE = "FILE: {} --{}"


class gs_show_file_at_commit(WindowCommand, GitCommand):

    def run(self, commit_hash, filepath, check_for_renames=False, lineno=1, lang=None):
        enqueue_on_worker(
            self.run_impl,
            commit_hash,
            filepath,
            check_for_renames,
            lineno,
            lang,
        )

    def run_impl(self, commit_hash, file_path, check_for_renames=False, lineno=1, lang=None):
        # need to get repo_path before the new view is created.
        repo_path = self.repo_path
        view = util.view.get_scratch_view(self, "show_file_at_commit")
        settings = view.settings()
        settings.set("git_savvy.show_file_at_commit_view.commit", commit_hash)
        settings.set("git_savvy.file_path", file_path)
        settings.set("git_savvy.repo_path", repo_path)
        if not lang:
            lang = util.file.get_syntax_for_file(file_path)
        nice_hash = self.get_short_hash(commit_hash) if len(commit_hash) >= 40 else commit_hash
        title = SHOW_COMMIT_TITLE.format(
            os.path.basename(file_path),
            nice_hash,
        )

        view.set_syntax_file(lang)
        view.set_name(title)

        if check_for_renames:
            file_path = self.filename_at_commit(file_path, commit_hash)

        text = self.get_file_content_at_commit(file_path, commit_hash)
        enqueue_on_ui(self.render_text, view, text, lineno)

    def render_text(self, view, text, lineno):
        replace_view_content(view, text)
        util.view.move_cursor(view, lineno, 0)


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
