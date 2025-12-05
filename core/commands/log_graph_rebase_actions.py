from contextlib import contextmanager
from functools import lru_cache, partial
from itertools import chain
import os
import re
import shlex

import sublime
import sublime_plugin

from GitSavvy.common import util
from GitSavvy.core.base_commands import GsTextCommand, GsWindowCommand, ask_for_branch
from GitSavvy.core.commands import log_graph
from GitSavvy.core.fns import filter_, unique
from GitSavvy.core.git_command import GitCommand, GitSavvyError
from GitSavvy.core.parse_diff import TextRange
from GitSavvy.core.runtime import on_new_thread, run_on_new_thread, throttled
from GitSavvy.core.ui_mixins.input_panel import show_single_line_input_panel
from GitSavvy.core.ui__quick_panel import noop, show_actions_panel, SEPARATOR
from GitSavvy.core.utils import flash, yes_no_switch
from GitSavvy.core.view import replace_view_content
from . import multi_selector


__all__ = (
    "gs_rebase_action",
    "gs_log_graph_add_previous_tip",
    "gs_log_graph_remove_previous_tip",
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
    "gs_rebase_squash_commits",
    "gs_rebase_extract_commits",
    "AwaitTodoListView"
)


from typing import (
    Callable,
    Dict,
    List,
    Iterator,
    NamedTuple,
    Optional,
    Tuple,
    TypeVar,
)
from GitSavvy.core.base_commands import GsCommand, Args, Kont
_T = TypeVar("_T")


class Commit(NamedTuple):
    commit_hash: str
    commit_message: str


QuickAction = Callable[[str], str]
VERSION_WITH_UPDATE_REFS = (2, 38, 0)


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


ask_for_branch_ = ask_for_branch(
    ask_remote_first=False,
    ignore_current_branch=True,
    memorize_key="last_branch_used_to_rebase_from"
)


def ask_for_ref(self, args, done, initial_text=""):
    # type: (GsTextCommand, Args, Kont, str) -> None
    def on_done(ref):
        ref = ref.strip().replace(" ", "-")
        if not ref:
            return

        if not ref.startswith("refs/"):
            branch_name = ref
            ref = f"refs/heads/{ref}"
            for branch in self.get_local_branches():
                if branch.name == branch_name:
                    if branch.active:
                        show_actions_panel(self.window, [
                            (
                                f"Abort, {branch_name} is your current branch which can't be overwritten.",
                                lambda: ask_for_ref(self, args, done, initial_text=branch_name)
                            )
                        ])
                    else:
                        show_actions_panel(self.window, [
                            (
                                f"Abort, {branch_name} would be overwritten.",
                                lambda: ask_for_ref(self, args, done, initial_text=branch_name)
                            ),
                            (
                                "Go ahead!",
                                lambda: done(ref)
                            )
                        ])
                    return
        done(ref)

    view = self.view
    if (
        not initial_text
        and view.settings().get("git_savvy.log_graph_view")
        and (frozen_sel := list(multi_selector.get_selection(view)))
        and (line := log_graph.line_from_pt(view, frozen_sel[0].begin()))
        and (info := log_graph.describe_graph_line(line.text, known_branches={}))
        # As we don't feed in `known_branches`, every branch lands in `branches`.
        # We're actually only interested in `local_branches` but this is also
        # only the initial text, so we probably don't need to be more accurate here.
        and (branches := info.get("branches", []))
    ):
        initial_text = branches[-1]
        if initial_text.endswith("--local"):
            initial_text = initial_text[:-7]

    show_single_line_input_panel(
        "Branch name or ref:",
        initial_text,
        on_done,
    )


def take_current_branch_name(cmd, args, done):
    # type: (GsTextCommand, Args, Kont) -> None
    current_branch_name = cmd.get_current_branch_name()
    if current_branch_name:
        done(current_branch_name)
    else:
        cmd.window.status_message("Not in detached state.")


def get_view_for_command(cmd):
    # type: (sublime_plugin.Command) -> Optional[sublime.View]
    if isinstance(cmd, sublime_plugin.TextCommand):
        return cmd.view
    elif isinstance(cmd, sublime_plugin.WindowCommand):
        return cmd.window.active_view()
    else:
        return sublime.active_window().active_view()


class gs_rebase_action(GsWindowCommand):
    selected_index = 0

    def run(self):
        # type: () -> None
        view = self.window.active_view()
        if not view:
            return

        infos = list(filter_(
            log_graph.describe_graph_line(line, known_branches={})
            for line in unique(
                view.substr(line)
                for s in multi_selector.get_selection(view)
                for line in view.lines(s)
            )
        ))
        if not infos:
            flash(view, "Not on a line with a commit.")
            return

        def multi_commits_actions():
            actions = []  # type: List[Tuple[str, Callable[[], None]]]

            commit_hashes = list(reversed([
                info["commit"]
                for info in infos
            ]))
            formatted_commit_hashes = log_graph.format_revision_list(commit_hashes)

            actions += [
                (
                    f"Squash {formatted_commit_hashes}",
                    partial(self.squash_commits, view, commit_hashes)
                )
            ]
            if self.git_version >= VERSION_WITH_UPDATE_REFS:
                actions += [
                    (
                        "Copy {} to a <new branch> onto <branch>".format(formatted_commit_hashes),
                        partial(self.copy_commits, view, commit_hashes)
                    ),
                    (
                        "Extract {} to a <new branch> onto <branch>".format(formatted_commit_hashes),
                        partial(self.extract_commits, view, commit_hashes)
                    ),
                ]
            return actions

        def single_commit_actions():
            line = log_graph.line_from_pt(view, view.sel()[0].b)
            info = infos[0]
            commit_hash = info["commit"]
            commitish = commitish_from_info(info)
            parent_commitish = "{}^".format(commitish)
            commit_message = commit_message_from_line(view, line)
            on_head = "HEAD" in info
            actions = []  # type: List[Tuple[str, Callable[[], None]]]

            if commit_message:
                if log_graph.is_fixup_or_squash_message(commit_message):
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
                else:
                    base_dot = log_graph.dot_from_line(view, line)
                    if base_dot:
                        base_commit = commit_hash
                        dots = log_graph.find_fixups_upwards(base_dot, commit_message)
                        fixups = [
                            Commit(commit_hash_from_dot(view, dot), message)
                            for dot, message in dots
                        ]
                        if fixups:
                            formatted_commit_hashes = log_graph.format_revision_list(
                                [fixup.commit_hash for fixup in fixups]
                            )
                            if len(fixups) == 1:
                                formatted_commit_hashes = f"'{formatted_commit_hashes}'"

                            actions += [
                                (
                                    "Apply fixup{} {} to {}".format(
                                        "" if len(fixups) == 1 else "s",
                                        formatted_commit_hashes,
                                        base_commit
                                    )
                                    if any(is_fixup(fixup) for fixup in fixups)
                                    else f"Squash with {formatted_commit_hashes}",
                                    partial(self.apply_fixup, view, base_commit, fixups)
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
                    "Copy commit to a <new branch> onto <branch>",
                    partial(self.copy_commits, view, [commit_hash])
                ),
                (
                    "Extract commit to a <new branch> onto <branch>",
                    partial(self.extract_commits, view, [commit_hash])
                ),
                (
                    "Make fixup commit for {}".format(commit_hash),
                    partial(self.create_fixup_commit, commit_hash)
                ),
            ]

            on_off = lambda setting: "x" if setting else " "
            rebase_merges = _get_setting(self, "git_savvy.log_graph_view.rebase_merges", DEFAULT_VALUE)
            update_refs = _get_setting(self, "git_savvy.log_graph_view.update_refs", DEFAULT_VALUE)
            actions += [
                (
                    "———————————————————————————       [{}] --rebase-merges,   [{}] --update-refs"
                    .format(on_off(rebase_merges), on_off(update_refs)),
                    partial(self.switch_through_options, view, rebase_merges, update_refs)
                ),
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
                    "[R]ebase interactive from {} on".format(parent_commitish),
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
                actions += [
                    SEPARATOR,
                ]

                settings = view.settings()
                applying_filters = settings.get("git_savvy.log_graph_view.apply_filters")
                filters = (
                    settings.get("git_savvy.log_graph_view.filters", "")
                    if applying_filters
                    else ""
                )
                # General matcher:
                # (?P<branch_name>\S+)@{(?P<revision>\d+)}
                reflog_matcher = re.compile(
                    r"{}@{{(?P<revision>\d+)}}".format(re.escape(current_branch))
                )
                if previous_revisions := reflog_matcher.findall(filters):
                    if len(previous_revisions) > 1:
                        actions += [
                            (
                                "Hide previous tips of {} in the graph".format(current_branch),
                                partial(self.filter_filters, view, reflog_matcher)
                            )
                        ]
                    else:
                        actions += [
                            (
                                "Hide {}@{{{}}} in the graph"
                                .format(current_branch, previous_revisions[0]),
                                partial(self.filter_filters, view, reflog_matcher)
                            )
                        ]
                else:
                    actions += [
                        (
                            "Show previous tip of {} in the graph".format(current_branch),
                            partial(self.add_previous_tip, view, current_branch)
                        )
                    ]

                previous_tip = "{}@{{1}}".format(current_branch)
                actions += [
                    (
                        "Diff against previous tip",
                        partial(self.diff_commit, previous_tip, current_branch)
                    ),
                    (
                        "Compare with previous tip",
                        partial(self.compare_commits, current_branch, previous_tip)
                    ),

                ]

            return actions

        if len(infos) == 1:
            actions = single_commit_actions()
        else:
            actions = multi_commits_actions()

        def on_action_selection(index):
            if index == -1:
                return

            self.selected_index = index
            description, action = actions[index]
            action()

        selected_index = self.selected_index
        if 0 <= selected_index < len(actions) - 1:
            selected_action = actions[selected_index]
            if selected_action == SEPARATOR:
                selected_index += 1

        self.window.show_quick_panel(
            [a[0] for a in actions],
            on_action_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=selected_index,
        )

    def switch_through_options(self, view, rebase_merges, update_refs):
        current = (rebase_merges, update_refs)
        possible = [
            (True, True),
            (False, True),
            (False, False),
            (True, False),
        ]
        rebase_merges, update_refs = possible[(possible.index(current) + 1) % len(possible)]
        if update_refs:
            self.window.status_message("--update-refs requires git v2.38 or greater")

        view.settings().set("git_savvy.log_graph_view.rebase_merges", rebase_merges)
        view.settings().set("git_savvy.log_graph_view.update_refs", update_refs)
        self.window.run_command("gs_rebase_action")

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

    def squash_commits(self, view, commits):
        view.run_command("gs_rebase_squash_commits", {
            "base_commit": commits[0],
            "commits": commits[1:]
        })

    def extract_commits(self, view, commits):
        view.run_command("gs_rebase_extract_commits", {
            "commits": commits,
        })

    def copy_commits(self, view, commits):
        view.run_command("gs_rebase_extract_commits", {
            "commits": commits,
            "copy": True,
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

    def add_previous_tip(self, view, branch_name):
        view.run_command("gs_log_graph_add_previous_tip", {"branch_name": branch_name})

    def filter_filters(self, view, matcher: re.Pattern):
        settings = view.settings()
        filters = settings.get("git_savvy.log_graph_view.filters", "")
        new_filters = ' '.join(
            s for s in shlex.split(filters)
            if not matcher.search(s)
        )
        settings.set("git_savvy.log_graph_view.filters", new_filters)
        view.run_command("gs_log_graph_refresh")

    def compare_commits(self, base_commit, target_commit):
        self.window.run_command("gs_compare_commit", {
            "base_commit": base_commit,
            "target_commit": target_commit,
        })

    def diff_commit(self, base_commit, target_commit):
        self.window.run_command("gs_diff", {
            "in_cached_mode": False,
            "base_commit": base_commit,
            "target_commit": target_commit,
            "disable_stage": True
        })


def get_filters(view: sublime.View) -> str:
    settings = view.settings()
    return (
        settings.get("git_savvy.log_graph_view.filters", "")
        if settings.get("git_savvy.log_graph_view.apply_filters")
        else ""
    )


def set_filters(view: sublime.View, new_filters: str) -> None:
    settings = view.settings()
    settings.set("git_savvy.log_graph_view.filters", new_filters)
    if not settings.get("git_savvy.log_graph_view.apply_filters"):
        settings.set("git_savvy.log_graph_view.apply_filters", True)
        settings.set("git_savvy.log_graph_view.paths", [])


class gs_log_graph_add_previous_tip(GsTextCommand):
    defaults = {
        "branch_name": take_current_branch_name,
    }

    def run(self, edit, branch_name: str):
        view = self.view
        settings = view.settings()

        def probe_tip(ref: str) -> bool:
            try:
                commit_hash = self.git_throwing_silently(
                    "rev-parse", "--short", ref
                ).strip()
            except GitSavvyError as e:
                if "log for '{}'".format(branch_name) in e.stderr:
                    flash(view, "The branch '{}' has no older tip.".format(branch_name))
                else:
                    e.show_error_panel()
                    raise
                return False
            else:
                settings.set("git_savvy.log_graph_view.follow", commit_hash)
                return True

        filters = get_filters(view)
        reflog_matcher = re.compile(
            r"{}@{{(?P<revision>\d+)}}".format(re.escape(branch_name))
        )
        filters_splitted = shlex.split(filters)
        matches = [
            (idx, match) for idx, part in enumerate(filters_splitted)
            if (match := reflog_matcher.search(part))
        ]
        if matches:
            idx, last_ref = matches[-1]
            previous_tip = "{}@{{{}}}".format(branch_name, int(last_ref.group(1)) + 1)
            if not probe_tip(previous_tip):
                return

            new_filters = " ".join(
                previous_tip if idx == idx_ else part
                for idx_, part in enumerate(filters_splitted)
            )

        else:
            previous_tip = "{}@{{1}}".format(branch_name)
            if not probe_tip(previous_tip):
                return

            new_filters = ' '.join(filter_((filters, previous_tip)))

        set_filters(view, new_filters)
        view.run_command("gs_log_graph_refresh")


class gs_log_graph_remove_previous_tip(GsTextCommand):
    defaults = {
        "branch_name": take_current_branch_name,
    }

    def run(self, edit, branch_name: str):
        view = self.view
        settings = view.settings()
        filters = get_filters(view)

        reflog_matcher = re.compile(
            r"{}@{{(?P<revision>\d+)}}".format(re.escape(branch_name))
        )
        filters_splitted = shlex.split(filters)
        matches = [
            (idx, match) for idx, part in enumerate(filters_splitted)
            if (match := reflog_matcher.search(part))
        ]
        if matches:
            idx, last_ref = matches[-1]
            next_nr = int(last_ref.group(1)) - 1
            if next_nr > 0:
                previous_tip = "{}@{{{}}}".format(branch_name, next_nr)
                commit_hash = self.git("rev-parse", "--short", previous_tip).strip()
                settings.set("git_savvy.log_graph_view.follow", commit_hash)
            else:
                previous_tip = ""

            new_filters = " ".join(filter_(
                previous_tip if idx == idx_ else part
                for idx_, part in enumerate(filters_splitted)
            ))

        else:
            flash(self.view, f"There is no newer tip for `{branch_name}`.")
            return

        set_filters(view, new_filters)
        view.run_command("gs_log_graph_refresh")


def commit_message_from_line(view, line):
    # type: (sublime.View, TextRange) -> Optional[str]
    line_span = line.region()
    for r in log_graph.extract_message_regions(view):
        if line_span.contains(r):
            return line.text[(r.a - line_span.a):(r.b - line_span.a)]
    else:
        return None


def commit_hash_from_dot(view, dot):
    # type: (sublime.View, log_graph.colorizer.Char) -> str
    line = log_graph.line_from_pt(view, dot.pt)
    return log_graph.extract_commit_hash(line.text)


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
        after_rebase=None,
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
                            after_rebase=after_rebase,
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
                        return self.rebase(
                            "--root",  # <-- rebase from the root!
                            *[arg for arg in args if arg != commitish],
                            show_panel=show_panel,
                            custom_environ=custom_environ,
                            ok_message=ok_message,
                            offer_autostash=offer_autostash,
                            after_rebase=after_rebase,
                            **kwargs
                        )

        else:
            if show_panel and not self.in_rebase():
                auto_close_panel(window)
            if after_rebase:
                after_rebase()
            return rv

        finally:
            if self.in_rebase():
                window.status_message("rebase needs your attention")
            else:
                window.status_message(ok_message)
            util.view.refresh_gitsavvy_interfaces(window, refresh_sidebar=True)

    def _merge_commits_within_reach(self, commit_hash):
        # type: (str) -> str
        try:
            return self.git_throwing_silently(
                "log",
                "-1",
                "--merges",
                "--format=%H",
                "{}..".format(commit_hash),
            ).strip()
        except GitSavvyError as err:
            # Typically `commit_hash` refers a parent commit, e.g. "aedbea^",
            # and then throwing means we want to rewrite from a root commit.
            # Just try again without the "^" as the root commit can't be a
            # merge commit anyway.
            if (
                commit_hash.endswith("^") and
                f"fatal: ambiguous argument '{commit_hash}..': "
                f"unknown revision or path not in the working tree."
                in err.stderr
            ):
                return self._merge_commits_within_reach(commit_hash[:-1])

            err.show_error_panel()
            raise


def auto_close_panel(window, after=800):
    # type: (sublime.Window, int) -> None
    sublime.set_timeout(throttled(_close_panel, window), after)


def _close_panel(window):
    # type: (sublime.Window) -> None
    if window.active_panel() == "output.GitSavvy":
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
    rebase_merges = True
    update_refs = True
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
                start_commit = "{}^".format(commit_hash)
                self.rebase(
                    '--interactive',
                    (
                        yes_no_switch("--rebase-merges", self.rebase_merges)
                        if (
                            self.rebase_merges
                            and self._merge_commits_within_reach(start_commit)
                        ) else
                        None
                    ),
                    "--autostash",
                    yes_no_switch("--autosquash", self.autosquash),
                    (
                        yes_no_switch("--update-refs", self.update_refs)
                        if self.git_version >= VERSION_WITH_UPDATE_REFS else
                        None
                    ),
                    start_commit,
                    custom_environ=make_git_config_env({
                        "rebase.abbreviateCommands": "false"
                    }),
                    after_rebase=follow_new_commit(self, start_commit),
                )

        run_on_new_thread(program)


def make_git_config_env(config):
    # type: (Dict[str, str]) -> Dict[str, str]
    rv = {
        "GIT_CONFIG_COUNT": str(len(config))
    }
    for n, (key, value) in enumerate(config.items()):
        rv[f"GIT_CONFIG_KEY_{n}"] = key
        rv[f"GIT_CONFIG_VALUE_{n}"] = value

    return rv


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
                for commit in fixup_commits:
                    yield "{} {} {}\n".format(
                        "fixup" if is_fixup(commit) else "squash",
                        *commit
                    )
    return "".join(inner())


def squash_commits(commits: List[str], base_commit: str, buffer_content: str) -> str:
    def inner():
        # type: () -> Iterator[str]
        needle = "pick {} ".format(base_commit)
        to_squash = {"pick {} ".format(commit) for commit in commits}
        prefix_len = 6 + len(commits[0])  # 6 == len("pick  ")
        for line in buffer_content.splitlines():
            if line[:prefix_len] in to_squash:
                continue

            yield line
            if line.startswith(needle):
                for commit in commits:
                    yield f"squash {commit}"
        yield ""  # cosmetic trailing newline

    return "\n".join(inner())


class gs_rebase_edit_commit(gs_rebase_quick_action):
    action = partial(change_first_action, "edit")


class gs_rebase_drop_commit(gs_rebase_quick_action):
    action = partial(change_first_action, "drop")


class gs_rebase_reword_commit(gs_rebase_quick_action):
    action = partial(change_first_action, "reword")


class gs_rebase_apply_fixup(gs_rebase_quick_action):
    def run(self, edit, base_commit, fixes):  # type: ignore[override]
        self.action = partial(fixup_commits, [Commit(*fix) for fix in fixes])
        super().run(edit, base_commit)


class gs_rebase_squash_commits(gs_rebase_quick_action):
    def run(self, edit, base_commit, commits):  # type: ignore[override]
        self.action = partial(squash_commits, commits)
        super().run(edit, base_commit)


def copy_commits(ref: str, commits: List[str], buffer_content: str) -> str:
    def inner():
        yield "label onto"
        for commit in commits:
            yield f"pick {commit}"
        yield f"u {ref}"
        yield "reset onto"
        yield ""

        for line in buffer_content.splitlines():
            if line == f"update-ref {ref}":
                yield f"{line}--local"
            else:
                yield line

        yield ""  # cosmetic trailing newline

    return "\n".join(inner())


def extract_commits(ref: str, commits: List[str], buffer_content: str) -> str:
    def inner():
        yield "label onto"
        for commit in commits:
            yield f"pick {commit}"
        yield f"u {ref}"
        yield "reset onto"
        yield ""

        prefixes = {"pick {} ".format(commit) for commit in commits}
        prefix_len = 6 + len(commits[0])  # 6 == len("pick  ")
        for line in buffer_content.splitlines():
            if line == f"update-ref {ref}":
                continue
            elif line[:prefix_len] in prefixes:
                yield "drop" + line[4:]
            else:
                yield line

        yield ""  # cosmetic trailing newline

    return "\n".join(inner())


class gs_rebase_extract_commits(GsTextCommand, RebaseCommand):
    defaults = {
        "ref": ask_for_ref,
        "onto": ask_for_branch_,
    }

    def run(self, edit, ref, commits, onto, copy=False):
        # type: (sublime.Edit, str, List[str], str, bool) -> None
        def program():
            with await_todo_list(partial(copy_commits if copy else extract_commits, ref, commits)):
                self.rebase(
                    '--interactive',
                    (
                        "--rebase-merges"
                        if self._merge_commits_within_reach(onto)
                        else None
                    ),
                    "--autostash",
                    "--update-refs",
                    onto,
                    custom_environ=make_git_config_env({
                        "rebase.abbreviateCommands": "false"
                    }),
                    after_rebase=follow_new_commit(self, commits[0]),
                )

        run_on_new_thread(program)


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
                "--rebase-merges" if self._merge_commits_within_reach(commitish) else None,
                (
                    "--update-refs"
                    if self.git_version >= VERSION_WITH_UPDATE_REFS else
                    None
                ),
                commitish,
                custom_environ={"GIT_SEQUENCE_EDITOR": ":"},
                after_rebase=follow_new_commit(self, commitish),
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


def _get_setting(self, setting, default_value):
    # type: (GsCommand, str, _T) -> _T
    view = self._current_view()
    if not view:
        return default_value
    return view.settings().get(setting, default_value)


# `True` because both features actually _reduce_ the intrusiveness
# of the rebase.
DEFAULT_VALUE = True


def provide_rebase_merges(self, args, done):
    # type: (GsCommand, Args, Kont) -> None
    done(_get_setting(self, "git_savvy.log_graph_view.rebase_merges", DEFAULT_VALUE))


def provide_update_refs(self, args, done):
    # type: (GsCommand, Args, Kont) -> None
    done(
        _get_setting(self, "git_savvy.log_graph_view.update_refs", DEFAULT_VALUE)
        if self.git_version >= VERSION_WITH_UPDATE_REFS else
        None
    )


def follow_new_commit(self, commitish):
    # type: (GsTextCommand, str) -> Callable[[], None]
    """Set `follow` to the commit that got rewritten.

    Note that `commitish` is the start commit of the rebase, thus typically the
    parent of the interesting commit under the cursor.
    """
    settings = self.view.settings()
    if not settings.get("git_savvy.log_graph_view"):
        return lambda: None

    def sideeffect() -> None:
        if self.in_rebase():
            # On some commands, e.g. "edit", or generally on a merge conflict
            # the initial rebase command ends but the rebase is still ongoing.
            # In that case we must resolve somewhen later, currently in
            # `gs_log_graph_refresh`.
            settings.set("git_savvy.resolve_after_rebase", commitish)

        else:
            log_graph.resolve_commit_to_follow_after_rebase(self, commitish)
    return sideeffect


def follow_head(self):
    # type: (GsTextCommand) -> Callable[[], None]
    settings = self.view.settings()
    if not settings.get("git_savvy.log_graph_view"):
        return lambda: None

    def sideeffect() -> None:
        settings.set("git_savvy.log_graph_view.follow", "HEAD")
    return sideeffect


class gs_rebase_interactive(GsTextCommand, RebaseCommand):
    defaults = {
        "commitish": extract_parent_symbol_from_graph,
        "rebase_merges": provide_rebase_merges,
        "update_refs": provide_update_refs,
    }

    @on_new_thread
    def run(self, edit, commitish, rebase_merges, update_refs):
        # type: (sublime.Edit, str, Optional[bool], Optional[bool]) -> None
        self.rebase(
            '--interactive',
            (
                yes_no_switch("--rebase-merges", rebase_merges)
                if (
                    rebase_merges
                    and self._merge_commits_within_reach(commitish)
                ) else
                None
            ),
            yes_no_switch("--update-refs", update_refs),
            commitish,
            offer_autostash=True,
            after_rebase=follow_new_commit(self, commitish),
        )


class gs_rebase_interactive_onto_branch(GsTextCommand, RebaseCommand):
    defaults = {
        "commitish": extract_parent_symbol_from_graph,
        "onto": ask_for_branch_,
        "rebase_merges": provide_rebase_merges,
        "update_refs": provide_update_refs,
    }

    @on_new_thread
    def run(self, edit, commitish, onto, rebase_merges, update_refs):
        # type: (sublime.Edit, str, str, Optional[bool], Optional[bool]) -> None
        self.rebase(
            '--interactive',
            (
                yes_no_switch("--rebase-merges", rebase_merges)
                if (
                    rebase_merges
                    and self._merge_commits_within_reach(commitish)
                ) else
                None
            ),
            yes_no_switch("--update-refs", update_refs),
            commitish,
            "--onto",
            onto,
            offer_autostash=True,
            after_rebase=follow_head(self)
        )


class gs_rebase_on_branch(GsTextCommand, RebaseCommand):
    defaults = {
        "on": ask_for_branch_,
        "rebase_merges": provide_rebase_merges,
        "update_refs": provide_update_refs,
    }

    @on_new_thread
    def run(self, edit, on, rebase_merges, update_refs):
        # type: (sublime.Edit, str, Optional[bool], Optional[bool]) -> None
        self.rebase(
            (
                yes_no_switch("--rebase-merges", rebase_merges)
                if (
                    rebase_merges
                    and self._merge_commits_within_reach(on)
                ) else
                None
            ),
            yes_no_switch("--update-refs", update_refs),
            on,
            offer_autostash=True,
        )
