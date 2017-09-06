import os

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..git_command import GitCommand
from .diff import GsDiffOpenFileAtHunkCommand


SHOW_COMMIT_TITLE = "COMMIT: {}"


class GsShowCommitCommand(WindowCommand, GitCommand):

    def run(self, commit_hash):
        # need to get repo_path before the new view is created.
        repo_path = self.repo_path
        view = self.window.new_file()
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        view.set_syntax_file("Packages/GitSavvy/syntax/show_commit.sublime-syntax")
        view.settings().set("git_savvy.show_commit_view", True)
        view.settings().set("git_savvy.show_commit_view.commit", commit_hash)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.settings().set("git_savvy.show_commit_view.ignore_whitespace", False)
        view.settings().set("git_savvy.show_commit_view.show_word_diff", False)
        view.settings().set("git_savvy.show_commit_view.show_diffstat", savvy_settings.get("show_diffstat", True))
        view.set_name(SHOW_COMMIT_TITLE.format(self.get_short_hash(commit_hash)))
        view.set_scratch(True)
        view.run_command("gs_show_commit_refresh")
        view.run_command("gs_diff_navigate")
        view.run_command("gs_handle_vintageous")


class GsShowCommitRefreshCommand(TextCommand, GitCommand):

    def run(self, edit):
        commit_hash = self.view.settings().get("git_savvy.show_commit_view.commit")
        ignore_whitespace = self.view.settings().get("git_savvy.show_commit_view.ignore_whitespace")
        show_word_diff = self.view.settings().get("git_savvy.show_commit_view.show_word_diff")
        show_diffstat = self.view.settings().get("git_savvy.show_commit_view.show_diffstat")
        content = self.git(
            "show",
            "--ignore-all-space" if ignore_whitespace else None,
            "--word-diff" if show_word_diff else None,
            "--stat" if show_diffstat else None,
            "--patch",
            "--format=fuller",
            "--no-color",
            commit_hash)
        self.view.run_command("gs_replace_view_text", {"text": content, "nuke_cursors": True})
        self.view.set_read_only(True)


class GsShowCommitToggleSetting(TextCommand):

    """
    Toggle view settings: `ignore_whitespace` or `show_word_diff`.
    """

    def run(self, edit, setting):
        setting_str = "git_savvy.show_commit_view.{}".format(setting)
        settings = self.view.settings()
        settings.set(setting_str, not settings.get(setting_str))
        print("{} is now {}".format(setting, settings.get(setting_str)))
        self.view.run_command("gs_show_commit_refresh")


class GsShowCommitOpenFileAtHunkCommand(GsDiffOpenFileAtHunkCommand):

    """
    For each cursor in the view, identify the hunk in which the cursor lies,
    and open the file at that hunk in a separate view.
    """

    def load_file_at_line(self, filename, lineno):
        """
        Show file at target commit if `git_savvy.diff_view.target_commit` is non-empty.
        Otherwise, open the file directly.
        """
        commit_hash = self.view.settings().get("git_savvy.show_commit_view.commit")
        full_path = os.path.join(self.repo_path, filename)
        self.view.window().run_command("gs_show_file_at_commit", {
            "commit_hash": commit_hash,
            "filepath": full_path,
            "lineno": lineno
        })
