import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..git_command import GitCommand

SHOW_COMMIT_TITLE = "COMMIT: {}:{}"


class GsShowFileAtCommitCommand(WindowCommand, GitCommand):

    def run(self, commit_hash, filepath):
        self.filepath = filepath
        repo_path = self.repo_path
        currentview = self.window.active_view()
        lang = currentview.settings().get('syntax')
        view = self.window.new_file()
        view.set_syntax_file(lang)
        view.settings().set("commit_hash", commit_hash)
        view.settings().set("filepath", filepath)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.settings().set("word_wrap", False)
        view.settings().set("line_numbers", True)
        view.set_name(SHOW_COMMIT_TITLE.format(commit_hash[:7], filepath[len(repo_path)+1:]))
        view.set_scratch(True)
        sublime.set_timeout_async(lambda: self.render_text(view), 0)

    def render_text(self, view):
        commit_hash = view.settings().get("commit_hash")
        filepath = view.settings().get("filepath")
        repopath = view.settings().get("git_savvy.repo_path")
        filename = filepath[len(repopath)+1:]
        content = self.git("show", commit_hash + ':' + filename)
        view.run_command("gs_replace_view_text", {"text": content, "nuke_cursors": True})