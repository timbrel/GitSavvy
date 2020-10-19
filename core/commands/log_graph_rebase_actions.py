from collections import namedtuple
from contextlib import contextmanager
from functools import lru_cache, partial
from itertools import chain, takewhile
import os
import shlex

import sublime
import sublime_plugin

from GitSavvy.common import util
from GitSavvy.core.base_commands import GsTextCommand, GsWindowCommand
from GitSavvy.core.commands import log_graph
from GitSavvy.core.git_command import GitCommand, GitSavvyError
from GitSavvy.core.parse_diff import TextRange
from GitSavvy.core.runtime import on_new_thread, run_on_new_thread, throttled
from GitSavvy.core.ui_mixins.quick_panel import show_branch_panel
from GitSavvy.core.utils import flash
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
    from GitSavvy.core.base_commands import GsCommand, Kont

    RebaseItem = NamedTuple("RebaseItem", [
        ("action", str),
        ("commit_hash", str),
        ("commit_message", str)
    ])
    Commit = NamedTuple("Commit", [
        ("commit_hash", str),
        ("commit_message", str)
    ])
    QuickAction = Callable[[List[RebaseItem]], List[RebaseItem]]

else:
    RebaseItem = namedtuple("RebaseItem", "action commit_hash commit_message")
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


def extract_symbol_from_graph(self, done):
    # type: (GsCommand, Kont) -> None
    view = get_view_for_command(self)
    if not view:
        return
    sel = log_graph.get_simple_selection(view)
    if sel is None:
        flash(view, "Only single cursors are supported.")
        return

    line = log_graph.line_from_pt(view, sel.b)
    info = log_graph.describe_graph_line(line.text, remotes=[])
    if info is None:
        flash(view, "Not on a line with a commit.")
        return

    symbol = commitish_from_info(info)
    done(symbol)


def extract_parent_symbol_from_graph(self, done):
    # type: (GsCommand, Kont) -> None
    extract_symbol_from_graph(self, lambda symbol: done("{}^".format(symbol)))


def extract_commit_hash_from_graph(self, done):
    # type: (GsCommand, Kont) -> None
    view = get_view_for_command(self)
    if not view:
        return
    sel = log_graph.get_simple_selection(view)
    if sel is None:
        flash(view, "Only single cursors are supported.")
        return

    line = log_graph.line_from_pt(view, sel.b)
    info = log_graph.describe_graph_line(line.text, remotes=[])
    if info is None:
        flash(view, "Not on a line with a commit.")
        return

    commit_hash = info["commit"]
    done(commit_hash)


def ask_for_local_branch(self, done):
    # type: (GsCommand, Kont) -> None
    def on_done(branch):
        if branch:
            done(branch)

    show_branch_panel(
        on_done,
        local_branches_only=True,
        ignore_current_branch=True,
    )


def get_view_for_command(cmd):
    # type: (sublime_plugin.Command) -> Optional[sublime.View]
    if isinstance(cmd, sublime_plugin.TextCommand):
        return cmd.view
    elif isinstance(cmd, sublime_plugin.WindowCommand):
        return cmd.window.active_view()
    else:
        return sublime.active_window().active_view()


SEPARATOR = ("-" * 75, lambda: None)


class gs_rebase_action(GsWindowCommand, GitCommand):
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
        info = log_graph.describe_graph_line(line.text, remotes=[])
        if info is None:
            flash(view, "Not on a line with a commit.")
            return

        commit_hash = info["commit"]
        commitish = commitish_from_info(info)
        parent_commitish = "{}^".format(commitish)
        commit_message = commit_message_from_line(view, line)
        on_head = "HEAD" in info
        actions = []  # type: List[Tuple[str, Callable[[], None]]]

        if commit_message and is_fixup_or_squash_message(commit_message):
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
            SEPARATOR,
        ]

        # `HEAD^..HEAD` only selects one commit which is not enough
        # for autosquashing.
        if not on_head:
            head_info = log_graph.describe_head(view, [])
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
                "Rebase from {} on interactive".format(parent_commitish),
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

    original_message = strip_fixup_or_squash_prefix(commit_message)
    target_dot = log_graph.find_matching_commit(view.id(), dot, original_message)
    if not target_dot:
        return None

    target_line = log_graph.line_from_pt(view, target_dot.pt)
    target_commit_hash = log_graph.extract_commit_hash(target_line.text)
    return target_commit_hash


def is_fixup_or_squash_message(commit_message):
    # type: (str) -> bool
    return (
        commit_message.startswith("fixup! ")
        or commit_message.startswith("squash! ")
    )


def strip_fixup_or_squash_prefix(commit_message):
    # type: (str) -> str
    # As long as we process "visually", we must deal with
    # truncated messages which end with one or multiple dots
    # we have to strip.
    if commit_message.startswith('fixup! '):
        return commit_message[7:].rstrip('.').strip()
    if commit_message.startswith('squash! '):
        return commit_message[8:].rstrip('.').strip()
    return commit_message


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
        except GitSavvyError:
            ...
        else:
            if show_panel and not search_git_output(window, "rebase --continue"):
                auto_close_panel(window)
            return rv
        finally:
            if self.in_rebase():
                window.status_message("rebase needs your attention")
            else:
                window.status_message(ok_message)
            util.view.refresh_gitsavvy_interfaces(window, refresh_sidebar=True)


def search_git_output(window, needle):
    # type: (sublime.Window, str) -> bool
    view = window.find_output_panel("GitSavvy")
    if not view:
        return False

    return needle in view.substr(sublime.Region(0, view.size()))


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

            todo_items = extract_rebase_items_from_view(view)
            replace_view_content(view, format_rebase_items(action(todo_items)))
            view.run_command("save")
            view.close()


def extract_rebase_items_from_view(view):
    # type: (sublime.View) -> List[RebaseItem]
    buffer_content = view.substr(sublime.Region(0, view.size()))
    return [
        RebaseItem(*line.split(" ", 2))
        for line in takewhile(
            lambda line: bool(line.strip()),
            buffer_content.splitlines(keepends=True)
        )
    ]


def ensure_newline(text):
    # type: (str) -> str
    return text if text.endswith("\n") else "{}\n".format(text)


def format_rebase_items(items):
    # type: (List[RebaseItem]) -> str
    return "".join(
        ensure_newline(" ".join(item)) for item in items
    )


class gs_rebase_quick_action(GsTextCommand, RebaseCommand):
    action = None  # type: QuickAction
    autosquash = False
    defaults = {
        "commit_hash": extract_commit_hash_from_graph,
    }

    def run(self, edit, commit_hash):
        # type: (sublime.Edit, str) -> None
        action = self.action  # type: ignore[misc]
        if action is None:
            raise NotImplementedError("action must be defined")

        if not self.commit_is_ancestor_of_head(commit_hash):
            flash(self.view, "Selected commit is not part of the current branch.")
            return

        def program():
            with await_todo_list(action):  # type: ignore[arg-type]  # mypy bug
                self.rebase(
                    '--interactive',
                    "--autostash",
                    "--autosquash" if self.autosquash else "--no-autosquash",
                    "{}^".format(commit_hash),
                )

        run_on_new_thread(program)


def change_first_action(new_action, items):
    # type: (str, List[RebaseItem]) -> List[RebaseItem]
    return [items[0]._replace(action=new_action)] + items[1:]


def fixup_commits(fixup_commits, items):
    # type: (List[Commit], List[RebaseItem]) -> List[RebaseItem]
    fixup_commit_hashes = {commit.commit_hash for commit in fixup_commits}
    return [items[0]] + [
        RebaseItem(
            "fixup" if is_fixup(commit) else "squash",
            *commit
        )
        for commit in reversed(fixup_commits)
    ] + [
        item for item in items[1:]
        if item.commit_hash not in fixup_commit_hashes
    ]


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
        )


class gs_rebase_on_branch(GsTextCommand, RebaseCommand):
    defaults = {
        "on": ask_for_local_branch,
    }

    @on_new_thread
    def run(self, edit, on):
        # type: (sublime.Edit, str) -> None
        self.rebase(on)
