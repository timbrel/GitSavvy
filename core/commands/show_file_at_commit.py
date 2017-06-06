import re
import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from .log import LogMixin

SHOW_COMMIT_TITLE = "COMMIT: {}:{}"


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
        if not lang:
            lang = util.file.get_syntax_for_file(filepath)
        view.set_syntax_file(lang)
        view.settings().set("git_savvy.show_file_at_commit_view.commit", commit_hash)
        view.settings().set("git_savvy.file_path", filepath)
        view.settings().set("git_savvy.show_file_at_commit_view.lineno", lineno)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.set_name(SHOW_COMMIT_TITLE.format(self.get_short_hash(commit_hash), self.get_rel_path(filepath)))
        sublime.set_timeout_async(lambda: self.render_text(view), 0)

    def render_text(self, view):
        commit_hash = view.settings().get("git_savvy.show_file_at_commit_view.commit")
        filepath = self.file_path
        filename = self.get_rel_path(filepath)
        filename = re.sub('\\\\', '/', filename)
        filename = self.filename_at_commit(filename, commit_hash)
        content = self.git("show", commit_hash + ':' + filename)
        view.run_command("gs_replace_view_text", {"text": content, "nuke_cursors": True})
        lineno = view.settings().get("git_savvy.show_file_at_commit_view.lineno")
        util.view.move_cursor(view, lineno, 0)


class GsShowCurrentFileAtCommitCommand(GsShowFileAtCommitCommand):

    def run(self, commit_hash, lineno=1, lang=None):
        if not lang:
            lang = self.window.active_view().settings().get('syntax')
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

    def do_action(self, commit_hash):
        self.window.run_command("gs_show_current_file_at_commit", {
            "commit_hash": commit_hash
        })
