import sublime
from sublime_plugin import WindowCommand, TextCommand

from .base_command import BaseCommand
from ..common import github


COMMIT_HELP_TEXT = """

## To make a commit, type your commit message and press SUPER-ENTER.
## To cancel the commit, close the window.
"""

COMMIT_TITLE = "COMMIT"


class GgCommitCommand(WindowCommand, BaseCommand):

    def run(self, repo_path=None, include_unstaged=False, amend=False):
        repo_path = repo_path or self.repo_path
        view = self.window.new_file()
        view.settings().set("git_gadget.get_long_text_view", True)
        view.settings().set("git_gadget.commit_view.include_unstaged", include_unstaged)
        view.settings().set("git_gadget.commit_view.amend", amend)
        view.settings().set("git_gadget.repo_path", repo_path)
        view.set_name(COMMIT_TITLE)
        view.set_scratch(True)
        view.run_command("gg_commit_initialize_view")


class GgCommitInitializeViewCommand(TextCommand, BaseCommand):

    def run(self, edit):
        if self.view.settings().get("git_gadget.commit_view.amend"):
            last_commit_message = self.git("log", "-1", "--pretty=%B")
            initial_text = last_commit_message + COMMIT_HELP_TEXT
        else:
            initial_text = COMMIT_HELP_TEXT

        self.view.replace(edit, sublime.Region(0, 0), initial_text)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(0, 0))


class GgCommitViewDoCommitCommand(TextCommand, BaseCommand):

    def run(self, edit):
        view_text = self.view.substr(sublime.Region(0, self.view.size()))
        commit_message = view_text.replace(COMMIT_HELP_TEXT, "")

        if self.view.settings().get("git_gadget.commit_view.include_unstaged"):
            self.add_all_tracked_files()

        if self.view.settings().get("git_gadget.commit_view.amend"):
            self.git("commit", "-q", "--amend", "-F", "-", stdin=commit_message)
        else:
            self.git("commit", "-q", "-F", "-", stdin=commit_message)

        self.view.window().focus_view(self.view)
        self.view.window().run_command("close_file")


class GgShowGithubIssues(TextCommand, BaseCommand):

    def run(self, edit, other_repo=False):
        sublime.set_timeout_async(lambda: self.run_async(other_repo))

    def run_async(self, other_repo):
        default_remote_name, default_remote = self.get_remotes().popitem(last=False)
        remote = github.parse_remote(default_remote)

        if not other_repo:
            issues = github.get_issues(remote)
        else:
            # TODO
            issues = []

        if not issues:
            self.view.run_command("gg_insert_github_number", {"text": "#"})
            return

        self.menu_items = ["{} - {}".format(issue["number"], issue["title"]) for issue in issues]
        self.view.show_popup_menu(self.menu_items, self.on_done)

    def on_done(self, selection_id):
        if selection_id == -1:
            self.view.run_command("gg_insert_github_number", {"text": "#"})
        else:
            selection = self.menu_items[selection_id]
            number = selection.split(" ")[0]
            self.view.run_command("gg_insert_github_number", {"text": "#" + number})


class GgInsertGithubNumber(TextCommand, BaseCommand):

    def run(self, edit, text):
        text_len = len(text)
        selected_ranges = []

        for region in self.view.sel():
            selected_ranges.append((region.begin(), region.end()))
            self.view.replace(edit, region, text)

        self.view.sel().clear()
        self.view.sel().add_all([sublime.Region(begin + text_len, end + text_len) for begin, end in selected_ranges])
