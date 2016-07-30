import os
import re
import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..git_command import GitCommand
from ...common import util

SHOW_COMMIT_TITLE = "COMMIT: {}:{}"


class GsShowFileAtCommitCommand(WindowCommand, GitCommand):

    def run(self, commit_hash, filepath, lineno=1, lang=None):
        repo_path = self.repo_path
        view = util.view.get_scratch_view(self, "show_file_at_commit")
        if not lang:
            lang = util.file.get_syntax_for_file(filepath)
        view.set_syntax_file(lang)
        view.settings().set("git_savvy.show_file_at_commit_view.commit", commit_hash)
        view.settings().set("git_savvy.show_file_at_commit_view.filepath", filepath)
        view.settings().set("git_savvy.show_file_at_commit_view.lineno", lineno)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.set_name(SHOW_COMMIT_TITLE.format(commit_hash[:7], filepath[len(repo_path)+1:]))
        sublime.set_timeout_async(lambda: self.render_text(view), 0)

    def render_text(self, view):
        commit_hash = view.settings().get("git_savvy.show_file_at_commit_view.commit")
        filepath = view.settings().get("git_savvy.show_file_at_commit_view.filepath")
        repopath = view.settings().get("git_savvy.repo_path")
        filename = filepath[len(repopath)+1:]
        filename = re.sub('\\\\','/', filename)
        content = self.git("show", commit_hash + ':' + filename)
        view.run_command("gs_replace_view_text", {"text": content, "nuke_cursors": True})
        lineno = view.settings().get("git_savvy.show_file_at_commit_view.lineno")
        util.view.move_cursor(view, lineno, 0)
