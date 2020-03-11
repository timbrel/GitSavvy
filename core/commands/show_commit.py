import os

import sublime
from sublime_plugin import WindowCommand, TextCommand

from . import diff
from . import intra_line_colorizer
from ..git_command import GitCommand


MYPY = False
if MYPY:
    from typing import Optional

SHOW_COMMIT_TITLE = "COMMIT: {}"


class gs_show_commit(WindowCommand, GitCommand):

    def run(self, commit_hash):
        # need to get repo_path before the new view is created.
        repo_path = self.repo_path
        view = self.window.new_file()
        settings = view.settings()
        settings.set("git_savvy.show_commit_view", True)
        settings.set("git_savvy.show_commit_view.commit", commit_hash)
        settings.set("git_savvy.repo_path", repo_path)
        settings.set("git_savvy.show_commit_view.ignore_whitespace", False)
        settings.set("git_savvy.show_commit_view.show_word_diff", False)
        settings.set("git_savvy.show_commit_view.show_diffstat", self.savvy_settings.get("show_diffstat", True))
        view.set_syntax_file("Packages/GitSavvy/syntax/show_commit.sublime-syntax")
        nice_hash = self.get_short_hash(commit_hash) if len(commit_hash) >= 40 else commit_hash
        view.set_name(SHOW_COMMIT_TITLE.format(nice_hash))
        view.set_scratch(True)
        view.run_command("gs_show_commit_refresh")
        view.run_command("gs_handle_vintageous")


class gs_show_commit_refresh(TextCommand, GitCommand):

    def run(self, edit):
        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.show_commit_view.commit")
        ignore_whitespace = settings.get("git_savvy.show_commit_view.ignore_whitespace")
        show_word_diff = settings.get("git_savvy.show_commit_view.show_word_diff")
        show_diffstat = settings.get("git_savvy.show_commit_view.show_diffstat")
        content = self.git(
            "show",
            "--ignore-all-space" if ignore_whitespace else None,
            "--word-diff" if show_word_diff else None,
            "--stat" if show_diffstat else None,
            "--patch",
            "--format=fuller",
            "--no-color",
            commit_hash)
        self.view.run_command("gs_replace_view_text", {"text": content, "restore_cursors": True})
        self.view.set_read_only(True)
        intra_line_colorizer.annotate_intra_line_differences(self.view)


class gs_show_commit_toggle_setting(TextCommand):

    """
    Toggle view settings: `ignore_whitespace` or `show_word_diff`.
    """

    def run(self, edit, setting):
        setting_str = "git_savvy.show_commit_view.{}".format(setting)
        settings = self.view.settings()
        settings.set(setting_str, not settings.get(setting_str))
        self.view.window().status_message("{} is now {}".format(setting, settings.get(setting_str)))
        self.view.run_command("gs_show_commit_refresh")


class gs_show_commit_open_file_at_hunk(diff.GsDiffOpenFileAtHunkCommand):

    """
    For each cursor in the view, identify the hunk in which the cursor lies,
    and open the file at that hunk in a separate view.
    """

    def load_file_at_line(self, commit_hash, filename, row, col):
        # type: (Optional[str], str, int, int) -> None
        """
        Show file at target commit if `git_savvy.diff_view.target_commit` is non-empty.
        Otherwise, open the file directly.
        """
        if not commit_hash:
            print("Could not parse commit for its commit hash")
            return
        window = self.view.window()
        if not window:
            return

        full_path = os.path.join(self.repo_path, filename)
        short_hash = self.get_short_hash(commit_hash)
        if self.get_commit_hash_for_head(short=True) == short_hash:
            window.open_file(
                "{file}:{row}:{col}".format(file=full_path, row=row, col=col),
                sublime.ENCODED_POSITION
            )
        else:
            window.run_command("gs_show_file_at_commit", {
                "commit_hash": short_hash,
                "filepath": full_path,
                "lineno": row
            })


class gs_show_commit_show_hunk_on_head(diff.GsDiffOpenFileAtHunkCommand):
    def load_file_at_line(self, commit_hash, filename, row, col):
        # type: (Optional[str], str, int, int) -> None
        if not commit_hash:
            print("Could not parse commit for its commit hash")
            return
        window = self.view.window()
        if not window:
            return

        full_path = os.path.join(self.repo_path, filename)
        row = self.find_matching_lineno(commit_hash, "HEAD", row, full_path)
        window.open_file(
            "{file}:{row}:{col}".format(file=full_path, row=row, col=col),
            sublime.ENCODED_POSITION
        )


class gs_show_commit_open_graph_context(TextCommand, GitCommand):
    def run(self, edit):
        # type: (...) -> None
        window = self.view.window()
        if not window:
            return

        settings = self.view.settings()
        commit_hash = settings.get("git_savvy.show_commit_view.commit")

        window.run_command("gs_graph", {
            "all": True,
            "follow": self.get_short_hash(commit_hash)
        })
