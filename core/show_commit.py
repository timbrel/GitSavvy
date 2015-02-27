import sublime
from sublime_plugin import WindowCommand, TextCommand

from .base_command import BaseCommand


SHOW_COMMIT_TITLE = "COMMIT: {}"


class GsShowCommitCommand(WindowCommand, BaseCommand):

    def run(self, commit_hash):
        repo_path = self.repo_path
        view = self.window.new_file()
        view.set_syntax_file("Packages/GitSavvy/syntax/show_commit.tmLanguage")
        view.settings().set("git_savvy.show_commit_view", True)
        view.settings().set("git_savvy.show_commit_view.commit", commit_hash)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.settings().set("word_wrap", False)
        view.settings().set("line_numbers", False)
        view.set_name(SHOW_COMMIT_TITLE.format(commit_hash))
        view.set_scratch(True)
        view.run_command("gs_show_commit_initialize_view")


class GsShowCommitInitializeView(TextCommand, BaseCommand):

    def run(self, edit):
        commit_hash = self.view.settings().get("git_savvy.show_commit_view.commit")
        content = self.git("show", commit_hash)
        self.view.sel().clear()
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), content)
        self.view.set_read_only(True)

        start_of_diff = self.view.find("^diff ", 0).a
