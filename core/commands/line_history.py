import os

import sublime
from sublime_plugin import TextCommand, WindowCommand

from . import diff
from ..fns import filter_
from ..git_command import GitCommand
from ..parse_diff import CommitHeader, SplittedDiff
from ..runtime import enqueue_on_worker
from ..ui_mixins.quick_panel import LogHelperMixin
from ..utils import flash
from ..view import replace_view_content
from ...common import util


__all__ = (
    "gs_line_history",
    "gs_open_line_history",
    "gs_line_history_open_commit",
    "gs_line_history_open_graph_context",
    "gs_line_history_initiate_fixup_commit",
)


MYPY = False
if MYPY:
    from typing import List, Optional, Tuple
    from ..types import LineNo

    LineRange = Tuple[LineNo, LineNo]


class gs_line_history(TextCommand, GitCommand):
    def run(self, edit):
        # type: (sublime.Edit) -> None
        view = self.view
        window = view.window()
        if not window:
            return

        repo_path = self.repo_path
        file_path = view.file_name()
        if not file_path:
            flash(view, "Not available for unsaved files.")
            return

        if view.is_dirty():
            flash(view, "Hint: For unsaved files the line selection is probably not correct.")

        ranges = [
            (line_on_point(view, s.begin()), line_on_point(view, s.end()))
            for s in view.sel()
        ]
        if not ranges:
            flash(view, "No cursor to compute a line range from.")
            return

        diff = self.no_context_diff(None, "HEAD", file_path)
        ranges = [
            (
                self.adjust_line_according_to_diff(diff, a),
                self.adjust_line_according_to_diff(diff, b)
            )
            for a, b in ranges
        ]

        window.run_command("gs_open_line_history", {
            "repo_path": repo_path,
            "file_path": file_path,
            "ranges": ranges,
        })


def line_on_point(view, pt):
    # type: (sublime.View, int) -> LineNo
    row, _ = view.rowcol(pt)
    return row + 1


class gs_open_line_history(WindowCommand, GitCommand):
    def run(self, repo_path, file_path, ranges, commit=None):
        # type: (str, str, List[LineRange], str) -> None
        view = util.view.get_scratch_view(self, "line_history")
        settings = view.settings()
        settings.set("git_savvy.repo_path", repo_path)
        settings.set("git_savvy.file_path", file_path)

        settings.set("result_file_regex", diff.FILE_RE)
        settings.set("result_line_regex", diff.LINE_RE)
        settings.set("result_base_dir", repo_path)

        rel_file_path = os.path.relpath(file_path, repo_path)
        title = ''.join(
            [
                'LOG: {}'.format(
                    rel_file_path
                    + ('@{}'.format(commit) if commit else '')
                )
            ]
            + [' L{}-{}'.format(lr[0], lr[1]) for lr in ranges]
        )
        view.set_name(title)
        view.set_syntax_file("Packages/GitSavvy/syntax/show_commit.sublime-syntax")

        def render():
            normalized_short_filename = rel_file_path.replace('\\', '/')
            cmd = (
                [
                    'log',
                    '--decorate',
                    "--format=fuller",
                ]
                + [
                    '-L{},{}:{}'.format(lr[0], lr[1], normalized_short_filename)
                    for lr in ranges
                ]
                + [commit]  # type: ignore[list-item]  # mypy bug
            )
            output = self.git(*cmd)
            replace_view_content(view, output)

        enqueue_on_worker(render)


class gs_line_history_open_commit(TextCommand, GitCommand):
    def run(self, edit):
        # type: (sublime.Edit) -> None
        view = self.view
        window = view.window()
        if not window:
            return

        diff = SplittedDiff.from_view(view)
        commits = filter_(commit_hash_before_pt(diff, s.begin()) for s in view.sel())
        for c in commits:
            window.run_command('gs_show_commit', {'commit_hash': c})


class gs_line_history_open_graph_context(TextCommand, GitCommand):
    def run(self, edit):
        # type: (sublime.Edit) -> None
        view = self.view
        window = view.window()
        if not window:
            return

        diff = SplittedDiff.from_view(view)
        commit_hash = commit_hash_before_pt(diff, view.sel()[0].begin())
        if commit_hash:
            window.run_command("gs_graph", {
                "all": True,
                "follow": self.get_short_hash(commit_hash)
            })


def commit_before_pt(diff, pt):
    # type: (SplittedDiff, int) -> Optional[CommitHeader]
    for commit_header in reversed(diff.commits):
        if commit_header.a <= pt:
            return commit_header
    else:
        return None


def commit_hash_before_pt(diff, pt):
    # type: (SplittedDiff, int) -> Optional[str]
    commit_header = commit_before_pt(diff, pt)
    return commit_header.commit_hash() if commit_header else None


class gs_line_history_initiate_fixup_commit(TextCommand, LogHelperMixin):
    def run(self, edit):
        view = self.view
        window = view.window()
        assert window

        diff = SplittedDiff.from_view(view)
        commit_header = commit_before_pt(diff, view.sel()[0].begin())
        if not commit_header:
            flash(view, "No commit header found around the cursor.")
            return

        for r in view.find_by_selector("meta.commit_message meta.subject.git.commit"):
            if r.a > commit_header.a:
                commit_message = view.substr(r).strip()
                window.run_command("gs_commit", {
                    "initial_text": "fixup! {}".format(commit_message)
                })
                break
        else:
            flash(view, "Could not extract commit message subject")
