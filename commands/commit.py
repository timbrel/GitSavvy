import re

import sublime
from sublime_plugin import WindowCommand, TextCommand

from .base_command import BaseCommand
from ..common import github


COMMIT_HELP_TEXT = """

## To make a commit, type your commit message and press SUPER-ENTER.
## To cancel the commit, close the window.
"""

COMMIT_TITLE = "COMMIT"


class GsCommitCommand(WindowCommand, BaseCommand):

    def run(self, repo_path=None, include_unstaged=False, amend=False):
        repo_path = repo_path or self.repo_path
        view = self.window.new_file()
        view.settings().set("git_savvy.get_long_text_view", True)
        view.settings().set("git_savvy.commit_view.include_unstaged", include_unstaged)
        view.settings().set("git_savvy.commit_view.amend", amend)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.set_name(COMMIT_TITLE)
        view.set_scratch(True)
        view.run_command("gs_commit_initialize_view")


class GsCommitInitializeViewCommand(TextCommand, BaseCommand):

    def run(self, edit):
        if self.view.settings().get("git_savvy.commit_view.amend"):
            last_commit_message = self.git("log", "-1", "--pretty=%B")
            initial_text = last_commit_message + COMMIT_HELP_TEXT
        else:
            initial_text = COMMIT_HELP_TEXT

        self.view.replace(edit, sublime.Region(0, 0), initial_text)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(0, 0))


class GsCommitViewDoCommitCommand(TextCommand, BaseCommand):

    def run(self, edit):
        view_text = self.view.substr(sublime.Region(0, self.view.size()))
        commit_message = view_text.replace(COMMIT_HELP_TEXT, "")

        if self.view.settings().get("git_savvy.commit_view.include_unstaged"):
            self.add_all_tracked_files()

        if self.view.settings().get("git_savvy.commit_view.amend"):
            self.git("commit", "-q", "--amend", "-F", "-", stdin=commit_message)
        else:
            self.git("commit", "-q", "-F", "-", stdin=commit_message)

        self.view.window().focus_view(self.view)
        self.view.window().run_command("close_file")


class GsShowGithubIssuesCommand(TextCommand, BaseCommand):

    def run(self, edit, default_repo=True):
        if not default_repo:
            first_cursor = self.view.sel()[0].begin()
            text_before_cursor = self.view.substr(sublime.Region(0, first_cursor))
            nondefault_repo = re.search(r"([a-zA-Z\-_0-9\.]+)/([a-zA-Z\-_0-9\.]+)$", text_before_cursor).groups()
        else:
            nondefault_repo = None

        sublime.set_timeout_async(lambda: self.run_async(nondefault_repo))

    def run_async(self, nondefault_repo):
        default_remote_name, default_remote = self.get_remotes().popitem(last=False)
        remote = github.parse_remote(default_remote)

        if nondefault_repo:
            owner, repo_name = nondefault_repo
            remote = github.GitHubRepo(
                url="",
                fqdn=remote.fqdn,
                owner=owner,
                repo=repo_name,
                token=remote.token
            )

        issues = github.get_issues(remote)

        if not issues:
            self.view.run_command("gs_insert_gh_text", {"text": "#"})
            return

        self.menu_items = ["{} - {}".format(issue["number"], issue["title"]) for issue in issues]
        self.view.show_popup_menu(self.menu_items, self.on_done)

    def on_done(self, selection_id):
        if selection_id == -1:
            self.view.run_command("gs_insert_gh_text", {"text": "#"})
        else:
            selection = self.menu_items[selection_id]
            number = selection.split(" ")[0]
            self.view.run_command("gs_insert_gh_text", {"text": "#" + number})


class GsInsertGhTextCommand(TextCommand, BaseCommand):

    def run(self, edit, text):
        text_len = len(text)
        selected_ranges = []

        for region in self.view.sel():
            selected_ranges.append((region.begin(), region.end()))
            self.view.replace(edit, region, text)

        self.view.sel().clear()
        self.view.sel().add_all([sublime.Region(begin + text_len, end + text_len) for begin, end in selected_ranges])


class GsShowGithubContributorsCommand(TextCommand, BaseCommand):

    def run(self, edit):
        sublime.set_timeout_async(lambda: self.run_async())

    def run_async(self):
        default_remote_name, default_remote = self.get_remotes().popitem(last=False)
        remote = github.parse_remote(default_remote)

        contributors = github.get_contributors(remote)

        if not contributors:
            self.view.run_command("gs_insert_gh_text", {"text": "@"})
            return

        self.menu_items = [contributor["login"] for contributor in contributors]
        self.view.show_popup_menu(self.menu_items, self.on_done)

    def on_done(self, selection_id):
        if selection_id == -1:
            self.view.run_command("gs_insert_gh_text", {"text": "@"})
        else:
            selection = self.menu_items[selection_id]
            self.view.run_command("gs_insert_gh_text", {"text": "@" + selection})
