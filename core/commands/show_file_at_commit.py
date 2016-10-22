import re
import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..git_command import GitCommand
from ...common import util

SHOW_COMMIT_TITLE = "COMMIT: {}:{}"


class GsShowFileAtCommitCommand(WindowCommand, GitCommand):

    def run(self, commit_hash, filepath, lineno=1, lang=None):
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
        view.set_name(SHOW_COMMIT_TITLE.format(commit_hash[:7], self.get_rel_path(filepath)))
        sublime.set_timeout_async(lambda: self.render_text(view), 0)

    def render_text(self, view):
        commit_hash = view.settings().get("git_savvy.show_file_at_commit_view.commit")
        filepath = self.file_path
        filename = self.get_rel_path(filepath)
        filename = re.sub('\\\\', '/', filename)
        content = self.git("show", commit_hash + ':' + filename)
        view.run_command("gs_replace_view_text", {"text": content, "nuke_cursors": True})
        lineno = view.settings().get("git_savvy.show_file_at_commit_view.lineno")
        util.view.move_cursor(view, lineno, 0)
