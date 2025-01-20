from contextlib import contextmanager
import os
import re
from webbrowser import open as open_in_browser

import sublime
from sublime_plugin import EventListener, TextCommand, WindowCommand

from . import diff
from . import intra_line_colorizer
from . import log_graph_rebase_actions
from . import show_commit_info
from . import show_file_at_commit
from ..fns import filter_, flatten, unique
from ..git_command import GitCommand
from ..utils import flash, flash_regions, focus_view, Cache
from ..parse_diff import SplittedDiff, TextRange
from ..runtime import ensure_on_ui, enqueue_on_worker
from ..view import replace_view_content, Position
from ...common import util

from GitSavvy.github import github
from GitSavvy.github.git_mixins import GithubRemotesMixin
from GitSavvy.common import interwebs


__all__ = (
    "gs_show_commit",
    "gs_show_commit_refresh",
    "gs_show_commit_open_on_github",
    "gs_show_commit_toggle_setting",
    "gs_show_commit_open_previous_commit",
    "gs_show_commit_open_next_commit",
    "gs_show_commit_open_file_at_hunk",
    "gs_show_commit_show_hunk_on_working_dir",
    "gs_show_commit_open_graph_context",
    "gs_show_commit_initiate_fixup_commit",
    "gs_show_commit_reword_commit",
    "gs_line_history_reword_commit",
    "gs_show_commit_edit_commit",
    "GsShowCommitCopyCommitMessageHelper",
)


from typing import Dict, Optional, Sequence, Tuple, Union
from GitSavvy.core.base_commands import GsCommand, Args, Kont
from GitSavvy.core.types import LineNo, ColNo


SUBLIME_SUPPORTS_REGION_ANNOTATIONS = int(sublime.version()) >= 4050
SHOW_COMMIT_TITLE = "SHOW-COMMIT: {}"


def compute_identifier_for_view(view):
    # type: (sublime.View) -> Optional[Tuple]
    settings = view.settings()
    return (
        settings.get('git_savvy.repo_path'),
        settings.get('git_savvy.show_commit_view.commit')
    ) if settings.get('git_savvy.show_commit_view') else None


class gs_show_commit(WindowCommand, GitCommand):

    def run(self, commit_hash):
        repo_path = self.repo_path
        if commit_hash in {"", "HEAD"}:
            commit_hash = self.git("rev-parse", "--short", "HEAD").strip()
        else:
            commit_hash = self.get_short_hash(commit_hash)

        this_id = (
            repo_path,
            commit_hash
        )
        for view in self.window.views():
            if compute_identifier_for_view(view) == this_id:
                focus_view(view)
                break
        else:
            title = SHOW_COMMIT_TITLE.format(commit_hash)
            view = util.view.create_scratch_view(self.window, "show_commit", {
                "title": title,
                "syntax": "Packages/GitSavvy/syntax/show_commit.sublime-syntax",
                "git_savvy.repo_path": repo_path,
                "git_savvy.show_commit_view.commit": commit_hash,
                "git_savvy.show_commit_view.ignore_whitespace": False,
                "git_savvy.show_commit_view.show_diffstat":
                    self.savvy_settings.get("show_diffstat", True),
                "result_file_regex": diff.FILE_RE,
                "result_line_regex": diff.LINE_RE,
                "result_base_dir": repo_path,
            })

            view.run_command("gs_show_commit_refresh")
            view.run_command("gs_handle_vintageous")


url_cache = Cache()  # type: Dict[str, str]


class gs_show_commit_refresh(TextCommand, GithubRemotesMixin, GitCommand):

    def run(self, edit):
        view = self.view
        settings = view.settings()
        commit_hash = settings.get("git_savvy.show_commit_view.commit")
        ignore_whitespace = settings.get("git_savvy.show_commit_view.ignore_whitespace")
        show_diffstat = settings.get("git_savvy.show_commit_view.show_diffstat")
        content = self.read_commit(
            commit_hash,
            show_diffstat=show_diffstat,
            ignore_whitespace=ignore_whitespace
        )
        replace_view_content(view, content)
        commit_details = self.commit_subject_and_date_from_patch(content)
        self.update_title(commit_details)
        show_commit_info.restore_view_state(view, commit_hash)
        intra_line_colorizer.annotate_intra_line_differences(view)
        if SUBLIME_SUPPORTS_REGION_ANNOTATIONS:
            url = url_cache.get(commit_hash)
            self.update_annotation_link(url)
            enqueue_on_worker(self.annotate_with_github_link, commit_hash)

    def update_title(self, commit_details) -> None:
        message = ", ".join(filter_((
            commit_details.short_hash,
            commit_details.subject,
            commit_details.date
        )))
        title = SHOW_COMMIT_TITLE.format(message)
        self.view.set_name(title)

    def annotate_with_github_link(self, commit):
        # type: (str) -> None
        try:
            remote_url = self.get_integrated_remote_url()
        except ValueError:
            return
        github_repo = github.parse_remote(remote_url)
        auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None
        url = "{}/commit/{}".format(github_repo.url, commit)
        try:
            response = interwebs.request_url("HEAD", url, auth=auth)
        except Exception:
            return

        def sink(url):
            if self.view.settings().get("git_savvy.show_commit_view.commit") == commit:
                self.update_annotation_link(url)

        if 200 <= response.status < 300:
            url_cache[commit] = url
            ensure_on_ui(sink, url)
        else:
            url_cache.pop(commit, None)
            ensure_on_ui(sink, None)

    def update_annotation_link(self, url):
        # type: (Optional[str]) -> None
        key = "link_to_github"
        regions = [sublime.Region(0)]
        annotations = [
            '<span class="shortcut-key">[n/p]</span>'
            '&nbsp;'
            '<a href="subl:gs_show_commit_open_next_commit">next</a>/'
            '<a href="subl:gs_show_commit_open_previous_commit">previous</a> commit',
        ]
        if url:
            regions += [sublime.Region(self.view.text_point(1, 0))]
            annotations += [
                '<span class="shortcut-key">[h]</span>'
                '&nbsp;'
                '<a href="{}">Open on GitHub</a>'
                .format(url)
            ]
        self.view.add_regions(
            key,
            regions,
            annotations=annotations,
            annotation_color="#aaa0",
            flags=sublime.RegionFlags.NO_UNDO
        )


class gs_show_commit_open_on_github(TextCommand, GithubRemotesMixin, GitCommand):
    def run(self, edit):
        commits = self.commits()
        try:
            remote_url = self.get_integrated_remote_url()
        except ValueError as exc:
            flash(self.view, str(exc))
            return

        github_repo = github.parse_remote(remote_url)
        auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None

        for commit in commits:
            url = "{}/commit/{}".format(github_repo.url, commit)
            try:
                response = interwebs.request_url("HEAD", url, auth=auth)
            except Exception as exc:
                flash(self.view, str(exc))
                return

            if 200 <= response.status < 300:
                open_in_browser(url)
            else:
                flash(self.view, "commit {} not found on {}".format(commit, github_repo.url))

    def commits(self):
        view = self.view
        settings = view.settings()
        commit = settings.get("git_savvy.show_commit_view.commit")
        if commit:
            yield commit
        else:
            diff = SplittedDiff.from_view(view)
            yield from unique(filter_(diff.commit_hash_before_pt(s.begin()) for s in view.sel()))


class gs_show_commit_initiate_fixup_commit(TextCommand):
    def run(self, edit):
        view = self.view
        window = view.window()
        assert window

        commit_header = SplittedDiff.from_view(view).commit_before_pt(view.sel()[0].begin())
        if not commit_header:
            flash(view, "No commit header found around the cursor.")
            return

        for r in view.find_by_selector("meta.commit_message meta.subject.git.commit"):
            if r.a > commit_header.a:
                commit_message = view.substr(r).strip()
                view.settings().set("initiated_fixup_commit", commit_message)
                window.run_command("gs_commit", {
                    "initial_text": "fixup! {}".format(commit_message)
                })
                break
        else:
            flash(view, "Could not extract commit message subject")


def extract_commit_hash(self, args, done):
    # type: (GsCommand, Args, Kont) -> None
    view = log_graph_rebase_actions.get_view_for_command(self)
    if not view:
        return

    diff = SplittedDiff.from_view(view)
    commit_hashes = set(filter_(
        diff.commit_hash_before_pt(pt)
        for pt in unique(flatten(view.sel()))
    ))

    if not commit_hashes:
        flash(view, "No commit header found around the cursor.")
        return
    elif len(commit_hashes) > 1:
        flash(view, "Multiple commits are selected.")
        return

    commit_hash = self.get_short_hash(commit_hashes.pop())
    done(commit_hash)


class gs_show_commit_reword_commit(log_graph_rebase_actions.gs_rebase_reword_commit):
    defaults = {
        "commit_hash": extract_commit_hash,
    }

    def rebase(self, *args, **kwargs):
        rv = super().rebase(*args, **kwargs)
        match = re.search(r"^\[detached HEAD (\w+)]", rv, re.M)
        if match is not None:
            view = self.view
            settings = view.settings()
            new_commit_hash = match.group(1)

            settings.set("git_savvy.show_commit_view.commit", new_commit_hash)
            view.run_command("gs_show_commit_refresh")
            flash(view, "Now on commit {}".format(new_commit_hash))

        return rv


class gs_line_history_reword_commit(log_graph_rebase_actions.gs_rebase_reword_commit):
    defaults = {
        "commit_hash": extract_commit_hash,
    }


class gs_show_commit_edit_commit(log_graph_rebase_actions.gs_rebase_edit_commit):
    defaults = {
        "commit_hash": extract_commit_hash,
    }


class gs_show_commit_toggle_setting(TextCommand):

    """
    Toggle view settings: `ignore_whitespace`.
    """

    def run(self, edit, setting):
        setting_str = "git_savvy.show_commit_view.{}".format(setting)
        settings = self.view.settings()
        settings.set(setting_str, not settings.get(setting_str))
        flash(self.view, "{} is now {}".format(setting, settings.get(setting_str)))
        self.view.run_command("gs_show_commit_refresh")


class gs_show_commit_open_previous_commit(TextCommand, GitCommand):
    def run(self, edit):
        # type: (...) -> None
        view = self.view
        window = view.window()
        if window:
            active_view = window.active_view()
            if active_view:
                if active_view.settings().get("git_savvy.log_graph_view"):
                    active_view.run_command("gs_log_graph_navigate", {"forward": False})
                    return

        settings = view.settings()
        file_path: Optional[str] = settings.get("git_savvy.file_path")
        commit_hash: str = settings.get("git_savvy.show_commit_view.commit")

        previous_commit = show_file_at_commit.get_previous_commit(self, view, commit_hash, file_path)
        if not previous_commit:
            flash(view, "No older commit found.")
            return

        show_commit_info.remember_view_state(view)
        settings.set("git_savvy.show_commit_view.commit", previous_commit)

        view.run_command("gs_show_commit_refresh")
        flash(view, "On commit {}".format(previous_commit))


class gs_show_commit_open_next_commit(TextCommand, GitCommand):
    def run(self, edit):
        # type: (...) -> None
        view = self.view
        window = view.window()
        if window:
            active_view = window.active_view()
            if active_view:
                if active_view.settings().get("git_savvy.log_graph_view"):
                    active_view.run_command("gs_log_graph_navigate", {"forward": True})
                    return

        settings = view.settings()
        file_path: Optional[str] = settings.get("git_savvy.file_path")
        commit_hash: str = settings.get("git_savvy.show_commit_view.commit")
        try:
            next_commit = show_file_at_commit.get_next_commit(self, view, commit_hash, file_path)
        except ValueError:
            flash(view, "Can't find a newer commit; it looks orphaned.")
            return

        if not next_commit:
            flash(view, "No newer commit found.")
            return

        show_commit_info.remember_view_state(view)
        settings.set("git_savvy.show_commit_view.commit", next_commit)

        view.run_command("gs_show_commit_refresh")
        flash(view, "On commit {}".format(next_commit))


class gs_show_commit_open_file_at_hunk(diff.gs_diff_open_file_at_hunk):

    """
    For each cursor in the view, identify the hunk in which the cursor lies,
    and open the file at that hunk in a separate view.
    """

    def load_file_at_line(self, commit_hash, filename, line, col):
        # type: (Optional[str], str, LineNo, ColNo) -> None
        """
        Show file at target commit if `git_savvy.diff_view.target_commit` is non-empty.
        Otherwise, open the file directly.
        """
        if not commit_hash:
            flash(self.view, "Could not parse commit for its commit hash")
            return
        window = self.view.window()
        if not window:
            return

        full_path = os.path.join(self.repo_path, filename)
        window.run_command("gs_show_file_at_commit", {
            "commit_hash": commit_hash,
            "filepath": full_path,
            "position": Position(line - 1, col - 1, None)
        })


class gs_show_commit_show_hunk_on_working_dir(diff.gs_diff_open_file_at_hunk):
    def load_file_at_line(self, commit_hash, filename, line, col):
        # type: (Optional[str], str, LineNo, ColNo) -> None
        if not commit_hash:
            flash(self.view, "Could not parse commit for its commit hash")
            return
        window = self.view.window()
        if not window:
            return

        full_path = os.path.join(self.repo_path, filename)
        line = self.find_matching_lineno(commit_hash, None, line, full_path)

        with force_remember_commit_info_panel_focus_state(window):
            view = window.open_file(
                "{file}:{line}:{col}".format(file=full_path, line=line, col=col),
                sublime.ENCODED_POSITION
            )
            # https://github.com/sublimehq/sublime_text/issues/4418
            # Sublime Text 4 focuses the view automatically *if* it
            # was already open, otherwise it makes the view only
            # visible. Force the focus for a consistent behavior.
            focus_view(view)


@contextmanager
def force_remember_commit_info_panel_focus_state(window):
    # Although we automatically detect when the panel loses its focus in `log_graph.py`,
    # it fails when `window.open_file` brought the file to front *without* focusing
    # it.  In that case, t.i. when it just *opens* the file, `focus_view()` will first
    # activate the graph view and *then* the file we just opened.  This looks like
    # a bug, probably related to https://github.com/sublimehq/sublime_text/issues/4418

    # When `gs_show_commit_show_hunk_on_working_dir` runs and the graph view is the
    # `active_view`, the panel has the focus as the command is only bound to the panel
    # view.
    av = window.active_view()
    had_focus = (
        av.settings().get("git_savvy.log_graph_view", False)
        if av
        else False
    )

    yield

    if had_focus:
        panel_view = window.find_output_panel('show_commit_info')
        if panel_view:
            panel_view.settings().set("git_savvy.show_commit_view.had_focus", True)


class gs_show_commit_open_graph_context(TextCommand, GitCommand):
    def run(self, edit):
        # type: (...) -> None
        window = self.view.window()
        if not window:
            return

        settings = self.view.settings()
        if settings.get("git_savvy.show_commit_view.belongs_to_a_graph"):
            av = window.active_view()
            if av:
                window.focus_view(av)
            return

        commit_hash = settings.get("git_savvy.show_commit_view.commit")
        window.run_command("gs_graph", {
            "all": True,
            "follow": self.get_short_hash(commit_hash)
        })


class GsShowCommitCopyCommitMessageHelper(EventListener):
    def on_text_command(self, view, command_name, args):
        # type: (sublime.View, str, Dict) -> Union[None, str]
        if command_name != "copy":
            return None

        frozen_sel = [r for r in view.sel()]
        if len(frozen_sel) != 1:
            return None

        sel = frozen_sel[0]
        if not view.match_selector(sel.begin(), "git-savvy.commit meta.commit_message"):
            return None

        if sel.empty():
            if not view.settings().get("copy_with_empty_selection"):
                return None
            sel = view.line(sel.a)

        selected_text = TextRange(view.substr(sel), *sel)
        by_line = [
            line[4:] if line.text.startswith("    ") else line
            for line in selected_text.lines(keepends=False)
        ]
        string_for_clipboard = "\n".join(line.text for line in by_line)
        clip_content = sublime.get_clipboard(2048)

        if string_for_clipboard == clip_content:
            set_clipboard_and_flash(view, selected_text.text, [selected_text.region()])
            return "noop"

        regions = [line.region() for line in by_line]
        set_clipboard_and_flash(view, string_for_clipboard, regions)
        return "noop"


def set_clipboard_and_flash(view, text, regions):
    # type: (sublime.View, str, Sequence[sublime.Region]) -> None
    sublime.set_clipboard(text)
    flash_regions(view, regions)
