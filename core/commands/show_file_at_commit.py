import os
import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from .log import LogMixin

SHOW_COMMIT_TITLE = "FILE: {} --{}"


class GsShowFileAtCommitCommand(WindowCommand, GitCommand):

    def run(self, commit_hash, filepath, lineno=1, lang=None):
        sublime.set_timeout_async(
            lambda: self.run_async(
                commit_hash=commit_hash,
                filepath=filepath,
                lineno=lineno,
                lang=lang))

    def run_async(self, commit_hash, filepath, lineno=1, lang=None):
        # need to get repo_path before the new view is created.
        repo_path = self.repo_path
        view = util.view.get_scratch_view(self, "show_file_at_commit")
        settings = view.settings()
        settings.set("git_savvy.show_file_at_commit_view.commit", commit_hash)
        settings.set("git_savvy.file_path", filepath)
        settings.set("git_savvy.repo_path", repo_path)
        if not lang:
            lang = util.file.get_syntax_for_file(filepath)
        nice_hash = self.get_short_hash(commit_hash) if len(commit_hash) >= 40 else commit_hash
        title = SHOW_COMMIT_TITLE.format(
            os.path.basename(filepath),
            nice_hash,
        )

        view.set_syntax_file(lang)
        view.set_name(title)

        text = self.get_file_content_at_commit(self.file_path, commit_hash)
        sublime.set_timeout(lambda: self.render_text(view, text, lineno))

    def render_text(self, view, text, lineno):
        view.run_command("gs_replace_view_text", {"text": text, "nuke_cursors": True})
        util.view.move_cursor(view, lineno, 0)


class GsShowCurrentFileAtCommitCommand(GsShowFileAtCommitCommand):

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


class GsShowCurrentFileCommand(LogMixin, WindowCommand, GitCommand):
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
