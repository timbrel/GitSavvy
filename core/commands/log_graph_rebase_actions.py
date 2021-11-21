from collections import namedtuple
from contextlib import contextmanager
from functools import lru_cache, partial
from itertools import chain
import os
import re
import shlex

import sublime
import sublime_plugin

from GitSavvy.common import util
from GitSavvy.core import store
from GitSavvy.core.base_commands import GsTextCommand, GsWindowCommand
from GitSavvy.core.commands import log_graph
from GitSavvy.core.fns import filter_
from GitSavvy.core.git_command import GitCommand, GitSavvyError
from GitSavvy.core.parse_diff import TextRange
from GitSavvy.core.runtime import on_new_thread, run_on_new_thread, throttled
from GitSavvy.core.ui_mixins.quick_panel import show_branch_panel
from GitSavvy.core.utils import flash, noop, show_actions_panel
from GitSavvy.core.view import replace_view_content


__all__ = (
    "gs_rebase_action",
    "gs_rebase_interactive",
    "gs_rebase_interactive_onto_branch",
    "gs_rebase_on_branch",
    "gs_rebase_abort",
    "gs_rebase_continue",
    "gs_rebase_skip",
    "gs_rebase_just_autosquash",
    "gs_rebase_edit_commit",
    "gs_rebase_drop_commit",
    "gs_rebase_reword_commit",
    "gs_rebase_apply_fixup",
    "AwaitTodoListView"
)


MYPY = False
if MYPY:
    from typing import (
        Callable,
        List,
        Iterator,
        NamedTuple,
        Optional,
        Tuple
    )
    from GitSavvy.core.base_commands import GsCommand, Args, Kont

    Commit = NamedTuple("Commit", [
        ("commit_hash", str),
        ("commit_message", str)
    ])
    QuickAction = Callable[[str], str]

else:
    Commit = namedtuple("Commit", "commit_hash commit_message")


def commitish_from_info(info):
    # type: (log_graph.LineInfo) -> str
    commit_hash = info["commit"]
    head = info.get("HEAD")
    on_a_branch = head != commit_hash
    return next(
        chain(
            [head] if head and on_a_branch else [],
            reversed(info.get("branches", [])),
            info.get("tags", []),
        ),
        commit_hash
    )


def extract_symbol_from_graph(self, args, done):
    # type: (GsCommand, Args, Kont) -> None
    view = get_view_for_command(self)
    if not view:
        return
    sel = log_graph.get_simple_selection(view)
    if sel is None:
        flash(view, "Only single cursors are supported.")
        return

    line = log_graph.line_from_pt(view, sel.b)
    info = log_graph.describe_graph_line(line.text, known_branches={})
    if info is None:
        flash(view, "Not on a line with a commit.")
        return

    symbol = commitish_from_info(info)
    done(symbol)


def extract_parent_symbol_from_graph(self, args, done):
    # type: (GsCommand, Args, Kont) -> None
    extract_symbol_from_graph(self, args, lambda val, **kw: done("{}^".format(val)))


def extract_commit_hash_from_graph(self, args, done):
    # type: (GsCommand, Args, Kont) -> None
    view = get_view_for_command(self)
    if not view:
        return
    sel = log_graph.get_simple_selection(view)
    if sel is None:
        flash(view, "Only single cursors are supported.")
        return

    line = log_graph.line_from_pt(view, sel.b)
    info = log_graph.describe_graph_line(line.text, known_branches={})
    if info is None:
        flash(view, "Not on a line with a commit.")
        return

    commit_hash = info["commit"]
    done(commit_hash)


def get_view_for_command(cmd):
    # type: (sublime_plugin.Command) -> Optional[sublime.View]
    if isinstance(cmd, sublime_plugin.TextCommand):
        return cmd.view
    elif isinstance(cmd, sublime_plugin.WindowCommand):
        return cmd.window.active_view()
    else:
        return sublime.active_window().active_view()


SEPARATOR = ("-" * 75, lambda: None)


class gs_rebase_action(GsWindowCommand):
    selected_index = 0

    def run(self):
        # type: () -> None
        view = self.window.active_view()
        if not view:
            return

        sel = log_graph.get_simple_selection(view)
        if sel is None:
            flash(view, "Only single cursors are supported.")
            return

        line = log_graph.line_from_pt(view, sel.b)
        info = log_graph.describe_graph_line(line.text, known_branches={})
        if info is None:
            flash(view, "Not on a line with a commit.")
            return

        commit_hash = info["commit"]
        commitish = commitish_from_info(info)
        parent_commitish = "{}^".format(commitish)
        commit_message = commit_message_from_line(view, line)
        on_head = "HEAD" in info
        actions = []  # type: List[Tuple[str, Callable[[], None]]]

        if commit_message and log_graph.is_fixup_or_squash_message(commit_message):
            base_commit = find_base_commit_for_fixup(view, line, commit_message)
            if base_commit:
                fixup_commit = Commit(commit_hash, commit_message)
                actions += [
                    (
                        "Apply fix to '{}'".format(base_commit)
                        if is_fixup(fixup_commit)
                        else "Squash with '{}'".format(base_commit),
                        partial(self.apply_fixup, view, base_commit, [fixup_commit])
                    )
                ]

        actions += [
            (
                "Re[W]ord commit message",
                partial(self.reword, view, commit_hash)
            ),
            (
                "[E]dit commit",
                partial(self.edit, view, commit_hash)
            ),
            (
                "Drop commit",
                partial(self.drop, view, commit_hash)
            ),
            (
                "Make fixup commit for {}".format(commit_hash),
                partial(self.create_fixup_commit, commit_hash)
            ),
            SEPARATOR,
        ]

        head_info = log_graph.describe_head(view, {})
        # `HEAD^..HEAD` only selects one commit which is not enough
        # for autosquashing.
        if not on_head:
            good_head_name = (
                "HEAD"
                if not head_info or head_info["HEAD"] == head_info["commit"]
                else head_info["HEAD"]
            )
            actions += [
                (
                    "Apply fixes and squashes {}..{}".format(parent_commitish, good_head_name),
                    partial(self.autosquash, view, parent_commitish),
                ),
            ]

        actions += [
            (
                "[R]ebase from {} on interactive".format(parent_commitish),
                partial(self.rebase_interactive, view, parent_commitish)
            ),
            (
                "Rebase {} --onto <branch>".format(parent_commitish),
                partial(self.rebase_onto, view, parent_commitish)
            ),
            (
                "Rebase on <branch>",
                partial(self.rebase_on, view)
            ),
        ]

        current_branch = (
            head_info["HEAD"]
            if head_info and head_info["HEAD"] != head_info["commit"]
            else None
        )
        if current_branch:
            settings = view.settings()
            applying_filters = settings.get("git_savvy.log_graph_view.apply_filters")
            filters = (
                settings.get("git_savvy.log_graph_view.filters", "")
                if applying_filters
                else ""
            )
            previous_tip = "{}@{{1}}".format(current_branch)
            if previous_tip not in filters:
                actions += [
                    (
                        "Show previous tip of {} in the graph".format(current_branch),
                        partial(self.add_previous_tip, view, previous_tip)
                    )
                ]

        def on_action_selection(index):
            if index == -1:
                return

            self.selected_index = index
            description, action = actions[index]
            action()

        self.window.show_quick_panel(
            [a[0] for a in actions],
            on_action_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=self.selected_index,
        )

    def create_fixup_commit(self, commit_hash):
        commit_message = self.git("log", "-1", "--pretty=format:%s", commit_hash).strip()
        self.window.run_command("gs_commit", {
            "initial_text": "fixup! {}".format(commit_message)
        })

    def apply_fixup(self, view, base_commit, fixup_commits):
        view.run_command("gs_rebase_apply_fixup", {
            "base_commit": base_commit,
            "fixes": fixup_commits
        })

    def reword(self, view, commit_hash):
        view.run_command("gs_rebase_reword_commit", {"commit_hash": commit_hash})

    def edit(self, view, commit_hash):
        view.run_command("gs_rebase_edit_commit", {"commit_hash": commit_hash})

    def drop(self, view, commit_hash):
        view.run_command("gs_rebase_drop_commit", {"commit_hash": commit_hash})

    def autosquash(self, view, commitish):
        view.run_command("gs_rebase_just_autosquash", {"commitish": commitish})

    def rebase_interactive(self, view, commitish):
        view.run_command("gs_rebase_interactive", {"commitish": commitish})

    def rebase_onto(self, view, commitish):
        view.run_command("gs_rebase_interactive_onto_branch", {"commitish": commitish})

    def rebase_on(self, view):
        view.run_command("gs_rebase_on_branch")

    def add_previous_tip(self, view, previous_tip):
        settings = view.settings()
        try:
            commit_hash = self.git_throwing_silently(
                "rev-parse", "--short", previous_tip
            ).strip()
        except GitSavvyError as e:
            branch_name = previous_tip.split("@")[0]
            if "log for '{}'".format(branch_name) in e.stderr:
                flash(view, "The branch '{}' has no previous tip.".format(branch_name))
                return
            else:
                e.show_error_panel()
                raise

        else:
            settings.set("git_savvy.log_graph_view.follow", commit_hash)

        applying_filters = settings.get("git_savvy.log_graph_view.apply_filters")
        filters = (
            settings.get("git_savvy.log_graph_view.filters", "")
            if applying_filters
            else ""
        )
        new_filters = ' '.join(filter_((filters, previous_tip)))
        settings.set("git_savvy.log_graph_view.apply_filters", True)
        settings.set("git_savvy.log_graph_view.filters", new_filters)
        if not applying_filters:
            settings.set("git_savvy.log_graph_view.paths", [])
            settings.set("git_savvy.log_graph_view.filter_by_author", "")

        view.run_command("gs_log_graph_refresh")


def commit_message_from_line(view, line):
    # type: (sublime.View, TextRange) -> Optional[str]
    line_span = line.region()
    for r in log_graph.extract_message_regions(view):
        if line_span.contains(r):
            return line.text[(r.a - line_span.a):(r.b - line_span.a)]
    else:
        return None


def find_base_commit_for_fixup(view, commit_line, commit_message):
    # type: (sublime.View, TextRange, str) -> Optional[str]
    dot = log_graph.dot_from_line(view, commit_line)
    if not dot:
        return None

    original_message = log_graph.strip_fixup_or_squash_prefix(commit_message)
    target_dot = next(log_graph.find_matching_commit(dot, original_message), None)
    if not target_dot:
        return None

    target_line = log_graph.line_from_pt(view, target_dot.pt)
    target_commit_hash = log_graph.extract_commit_hash(target_line.text)
    return target_commit_hash


def is_fixup(commit):
    # type: (Commit) -> bool
    return commit.commit_message.startswith("fixup")


class RebaseCommand(GitCommand):
    def rebase(
        self,
        *args,
        show_panel=True,
        custom_environ=None,
        ok_message="rebase finished",
        offer_autostash=False,
        **kwargs
    ):
        window = self.window  # type: ignore[attr-defined]
        editor = sublime_git_editor()
        environ = {
            "GIT_EDITOR": editor,
            "GIT_SEQUENCE_EDITOR": editor
        }
        if custom_environ:
            environ.update(custom_environ)

        # Python nut, if you return from the `try` clause the `else`
        # clause never runs!  So we capture `rv` here.
        try:
            rv = self.git(
                "rebase",
                *args,
                show_panel=show_panel,
                custom_environ=environ,
                **kwargs
            )
        except GitSavvyError as e:
            if (
                offer_autostash and
                "error: cannot rebase: You have unstaged changes." in e.stderr
            ):
                show_actions_panel(window, [
                    noop("Abort. You have unstaged changes."),
                    (
                        "Try again with '--autostash'.",
                        partial(
                            run_on_new_thread,
                            self.rebase,
                            "--autostash",
                            *args,
                            show_panel=show_panel,
                            custom_environ=custom_environ,
                            ok_message=ok_message,
                            offer_autostash=False,
                            **kwargs
                        )
                    )
                ])
                return

            match = re.search(r"fatal: invalid upstream '(.+\^)'", e.stderr)
            if match:
                commitish = match.group(1)
                # Ensure the commit doesn't have a parent before retrying with `--root`
                for line in self.git("cat-file", "-p", commitish[:-1]).splitlines():
                    if line.lower().startswith("parent "):
                        break  # Abort, the commit has a parent
                    if line.lower().startswith("author "):
                        # The commit does not have a parent as `author`
                        # comes *after* `parent` in a patch (`-p`).
                        run_on_new_thread(
                            self.rebase,
                            "--root",  # <-- rebase from the root!
                            *[arg for arg in args if arg != commitish],
                            show_panel=show_panel,
                            custom_environ=custom_environ,
                            ok_message=ok_message,
                            offer_autostash=offer_autostash,
                            **kwargs
                        )
                        return

        else:
            if show_panel and not self.in_rebase():
                auto_close_panel(window)
            return rv

        finally:
            if self.in_rebase():
                window.status_message("rebase needs your attention")
            else:
                window.status_message(ok_message)
            util.view.refresh_gitsavvy_interfaces(window, refresh_sidebar=True)


def auto_close_panel(window, after=800):
    # type: (sublime.Window, int) -> None
    sublime.set_timeout(throttled(_close_panel, window), after)


def _close_panel(window):
    # type: (sublime.Window) -> None
    window.run_command("hide_panel", {"panel": "output.GitSavvy"})


@lru_cache(1)
def sublime_git_editor():
    normalized_executable = get_sublime_executable().replace("\\", "/")
    return "{} -w".format(shlex.quote(normalized_executable))


def get_sublime_executable() -> str:
    executable_path = sublime.executable_path()
    if sublime.platform() == "osx":
        app_path = executable_path[: executable_path.rfind(".app/") + 5]
        executable_path = app_path + "Contents/SharedSupport/bin/subl"

    return executable_path


AWAITING = None  # type: Optional[QuickAction]


@contextmanager
def await_todo_list(action):
    # type: (QuickAction) -> Iterator[None]
    global AWAITING
    AWAITING = action
    try:
        yield
    finally:
        AWAITING = None


class AwaitTodoListView(sublime_plugin.EventListener):
    def on_activated(self, view):
        # type: (sublime.View) -> None
        global AWAITING
        if AWAITING is None:
            return
        action = AWAITING

        filename = view.file_name()
        if not filename:
            return
        if os.path.basename(filename) == "git-rebase-todo":
            AWAITING = None

            buffer_content = view.substr(sublime.Region(0, view.size()))
            modified_content = action(buffer_content)
            replace_view_content(view, modified_content)

            view.run_command("save")
            view.close()


class gs_rebase_quick_action(GsTextCommand, RebaseCommand):
    action = None  # type: QuickAction
    autosquash = False
    defaults = {
        "commit_hash": extract_commit_hash_from_graph,
    }

    def run(self, edit, commit_hash):
        # type: (sublime.Edit, str) -> None
        action = self.action
        if action is None:
            raise NotImplementedError("action must be defined")

        if not self.commit_is_ancestor_of_head(commit_hash):
            flash(self.view, "Selected commit is not part of the current branch.")
            return

        def program():
            with await_todo_list(partial(action, commit_hash)):
                self.rebase(
                    '--interactive',
                    "--autostash",
                    "--autosquash" if self.autosquash else "--no-autosquash",
                    "{}^".format(commit_hash),
                )

        run_on_new_thread(program)


def change_first_action(new_action, base_commit, buffer_content):
    # type: (str, str, str) -> str
    needle = "pick {} ".format(base_commit)
    return "".join(
        new_action + line[4:]  # replace "pick" with `new_action`; len("pick") == 4
        if line.startswith(needle)
        else line
        for line in buffer_content.splitlines(keepends=True)
        if not line.startswith("#")
    )


def fixup_commits(fixup_commits, base_commit, buffer_content):
    # type: (List[Commit], str, str) -> str
    def inner():
        # type: () -> Iterator[str]
        # The algorithm assumes that all commit hashes provided
        # have the same length, short or long
        needle = "pick {} ".format(base_commit)
        fixup_prefixes = {"pick {} ".format(commit.commit_hash) for commit in fixup_commits}
        prefix_len = len(fixup_commits[0].commit_hash) + 6  # len("pick  ") == 6
        for line in buffer_content.splitlines(keepends=True):
            if line.startswith("#") or line[:prefix_len] in fixup_prefixes:
                continue

            yield line
            if line.startswith(needle):
                for commit in reversed(fixup_commits):
                    yield "{} {} {}\n".format(
                        "fixup" if is_fixup(commit) else "squash",
                        *commit
                    )
    return "".join(inner())


class gs_rebase_edit_commit(gs_rebase_quick_action):
    action = partial(change_first_action, "edit")
    autosquash = False


class gs_rebase_drop_commit(gs_rebase_quick_action):
    action = partial(change_first_action, "drop")
    autosquash = False


class gs_rebase_reword_commit(gs_rebase_quick_action):
    action = partial(change_first_action, "reword")
    autosquash = False


class gs_rebase_apply_fixup(gs_rebase_quick_action):
    action = partial(fixup_commits)
    autosquash = False

    def run(self, edit, base_commit, fixes):
        self.action = partial(self.action, [Commit(*fix) for fix in fixes])
        super().run(edit, base_commit)


class gs_rebase_just_autosquash(GsTextCommand, RebaseCommand):
    defaults = {
        "commitish": extract_parent_symbol_from_graph,
    }

    def run(self, edit, commitish):
        # type: (sublime.Edit, str) -> None
        if not self.commit_is_ancestor_of_head(commitish):
            flash(self.view, "Selected commit is not part of the current branch.")
            return

        def program():
            self.rebase(
                '--interactive',
                "--autostash",
                "--autosquash",
                "{}".format(commitish),
                custom_environ={"GIT_SEQUENCE_EDITOR": ":"}
            )

        run_on_new_thread(program)


class gs_rebase_abort(sublime_plugin.WindowCommand, RebaseCommand):
    @on_new_thread
    def run(self):
        self.rebase('--abort', ok_message="rebase aborted")


class gs_rebase_continue(sublime_plugin.WindowCommand, RebaseCommand):
    @on_new_thread
    def run(self):
        self.rebase('--continue')


class gs_rebase_skip(sublime_plugin.WindowCommand, RebaseCommand):
    @on_new_thread
    def run(self):
        self.rebase('--skip')


class gs_rebase_interactive(GsTextCommand, RebaseCommand):
    defaults = {
        "commitish": extract_parent_symbol_from_graph,
    }

    @on_new_thread
    def run(self, edit, commitish):
        # type: (sublime.Edit, str) -> None
        self.rebase(
            '--interactive',
            "{}".format(commitish),
            offer_autostash=True,
        )


def ask_for_local_branch(self, args, done):
    # type: (GsCommand, Args, Kont) -> None
    def on_done(branch):
        store.update_state(self.repo_path, {"last_local_branch_for_rebase": branch})
        done(branch)

    selected_branch = store.current_state(self.repo_path).get("last_local_branch_for_rebase")
    show_branch_panel(
        on_done,
        local_branches_only=True,
        ignore_current_branch=True,
        selected_branch=selected_branch
    )


class gs_rebase_interactive_onto_branch(GsTextCommand, RebaseCommand):
    defaults = {
        "commitish": extract_parent_symbol_from_graph,
        "onto": ask_for_local_branch
    }

    @on_new_thread
    def run(self, edit, commitish, onto):
        # type: (sublime.Edit, str, str) -> None
        self.rebase(
            '--interactive',
            "{}".format(commitish),
            "--onto",
            onto,
            offer_autostash=True,
        )


class gs_rebase_on_branch(GsTextCommand, RebaseCommand):
    defaults = {
        "on": ask_for_local_branch,
    }

    @on_new_thread
    def run(self, edit, on):
        # type: (sublime.Edit, str) -> None
        self.rebase(on, offer_autostash=True)
