from __future__ import annotations
from functools import lru_cache
from itertools import chain, count
import os
import re
import shlex

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from . import log_graph_colorizer as colorizer
from . import multi_selector
from . import show_commit_info
from .log_graph_helper import (
    COMMIT_LINE,
    FIND_COMMIT_HASH,
    COMMIT_NODE_CHARS,
    GRAPH_HEIGHT,
)
from .log import gs_log
from ..base_commands import GsTextCommand
from ..fns import filter_, flatten, pairwise, partition, take
from ..git_command import GitCommand
from ..text_helper import Region, TextRange
from ..settings import GitSavvySettings
from ..runtime import (
    cooperative_thread_hopper,
    enqueue_on_ui,
    enqueue_on_worker,
    run_on_new_thread,
    text_command,
    HopperR
)
from ..view import (
    find_by_selector,
    join_regions,
    line_distance,
    replace_view_content,
    show_region,
)
from ..ui_mixins.input_panel import show_single_line_input_panel
from ..ui__quick_panel import show_quick_panel
from ..ui__toast_popup import show_toast
from ..ui_mixins.quick_panel import show_branch_panel
from ..utils import add_selection_to_jump_history, flash, focus_view, Cache
from ...common import util
from ...common.theme_generator import ThemeGenerator


__all__ = (
    "gs_graph",
    "gs_graph_current_file",
    "gs_graph_current_path",
    "gs_graph_pickaxe",
    "gs_log_graph",
    "gs_log_graph_tab_out",
    "gs_log_graph_tab_in",
    "gs_log_graph_current_branch",
    "gs_log_graph_all_branches",
    "gs_log_graph_by_author",
    "gs_log_graph_by_branch",
    "gs_log_graph_navigate",
    "gs_log_graph_navigate_wide",
    "gs_log_graph_navigate_to_head",
    "gs_log_graph_edit_branches",
    "gs_log_graph_edit_filters",
    "gs_input_handler_go_history",
    "gs_log_graph_reset_filters",
    "gs_log_graph_toggle_overview",
    "gs_log_graph_edit_files",
    "gs_log_graph_toggle_all_setting",
    "gs_log_graph_open_commit",
    "gs_log_graph_toggle_commit_info_panel",
    "gs_log_graph_show_and_focus_panel",
    "GsLogGraphCursorListener",
)

from typing import (
    Callable, Dict, Iterable, Iterator, List, Optional, Set, Sequence, Tuple,
    TypeVar, Union
)
T = TypeVar('T')

QUICK_PANEL_SUPPORTS_WANT_EVENT = int(sublime.version()) >= 4096
DOT_SCOPE = 'git_savvy.graph.dot'
DOT_ABOVE_SCOPE = 'git_savvy.graph.dot.above'
PATH_SCOPE = 'git_savvy.graph.path_char'
PATH_ABOVE_SCOPE = 'git_savvy.graph.path_char.above'
MATCHING_COMMIT_SCOPE = 'git_savvy.graph.matching_commit'
NO_FILTERS = ([], "", "")  # type: Tuple[List[str], str, str]


def compute_identifier_for_view(view):
    # type: (sublime.View) -> Optional[Tuple]
    settings = view.settings()
    if not settings.get('git_savvy.log_graph_view'):
        return None

    apply_filters = settings.get('git_savvy.log_graph_view.apply_filters')
    return (
        settings.get('git_savvy.repo_path'),
        (
            settings.get('git_savvy.log_graph_view.all_branches')
            or settings.get('git_savvy.log_graph_view.branches')
        ),
        (
            True if settings.get('git_savvy.log_graph_view.overview')
            else (
                settings.get('git_savvy.log_graph_view.paths'),
                settings.get('git_savvy.log_graph_view.filters'),
            ) if apply_filters
            else NO_FILTERS
        )
    )


class gs_graph(WindowCommand, GitCommand):
    def run(
        self,
        repo_path=None,
        file_path=None,
        overview=False,
        all=False,
        show_tags=True,
        branches=None,
        title='GRAPH',
        follow=None,
        decoration=None,
        filters='',
    ):
        if repo_path is None:
            repo_path = self.repo_path
        assert repo_path
        paths = (
            [self.get_rel_path(file_path) if os.path.isabs(file_path) else file_path]
            if file_path
            else []
        )
        if branches is None:
            branches = []
        apply_filters = paths or filters

        this_id = (
            repo_path,
            all or branches,
            True if overview else (paths, filters) if apply_filters else NO_FILTERS
        )
        for view in self.window.views():
            other_id = compute_identifier_for_view(view)
            standard_graph_views = (
                []
                if branches or overview
                else [(repo_path, True, NO_FILTERS), (repo_path, [], NO_FILTERS)]
            )
            if other_id in [this_id] + standard_graph_views:
                settings = view.settings()
                settings.set("git_savvy.log_graph_view.overview", overview)
                settings.set("git_savvy.log_graph_view.all_branches", all)
                settings.set("git_savvy.log_graph_view.show_tags", show_tags)
                settings.set("git_savvy.log_graph_view.branches", branches)
                if decoration is not None:
                    settings.set('git_savvy.log_graph_view.decoration', decoration)
                settings.set('git_savvy.log_graph_view.apply_filters', apply_filters)
                if apply_filters:
                    settings.set('git_savvy.log_graph_view.paths', paths)
                    settings.set('git_savvy.log_graph_view.filters', filters)
                if follow:
                    settings.set('git_savvy.log_graph_view.follow', follow)

                if follow and follow != extract_symbol_to_follow(view):
                    if show_commit_info.panel_is_visible(self.window):
                        # Hack to force a synchronous update of the panel
                        # *as a result of* `navigate_to_symbol` (by
                        # `on_selection_modified`) since we know that
                        # "show_commit_info" will run blocking if the panel
                        # is empty (or closed).
                        panel = show_commit_info.ensure_panel(self.window)
                        replace_view_content(panel, "")
                    navigate_to_symbol(view, follow)

                if self.window.active_view() != view:
                    focus_view(view)
                else:
                    view.run_command("gs_log_graph_refresh")
                break
        else:
            if follow is None:
                follow = "HEAD"
            if decoration is None:
                decoration = "sparse"
            show_commit_info_panel = bool(self.savvy_settings.get("graph_show_more_commit_info"))
            view = util.view.create_scratch_view(self.window, "log_graph", {
                "title": title,
                "syntax": "Packages/GitSavvy/syntax/graph.sublime-syntax",
                "git_savvy.repo_path": repo_path,
                "git_savvy.log_graph_view.paths": paths,
                "git_savvy.log_graph_view.overview": overview,
                "git_savvy.log_graph_view.all_branches": all,
                "git_savvy.log_graph_view.show_tags": show_tags,
                "git_savvy.log_graph_view.branches": branches,
                "git_savvy.log_graph_view.follow": follow,
                "git_savvy.log_graph_view.decoration": decoration,
                "git_savvy.log_graph_view.filters": filters,
                "git_savvy.log_graph_view.apply_filters": apply_filters,
                "git_savvy.log_graph_view.show_commit_info_panel": show_commit_info_panel,
            })
            view.run_command("gs_handle_vintageous")
            view.run_command("gs_handle_arrow_keys")
            run_on_new_thread(augment_color_scheme, view)

            # We need to ensure the panel has been created, so it appears
            # e.g. in the menu. Otherwise Sublime will not handle `show_panel`
            # events for that panel at all.
            # Note that the following is basically what `on_activated` does,
            # but `on_activated` runs synchronous when a view gets created t.i.
            # even before we can mark it as "graph_view" in the settings.
            show_commit_info.ensure_panel(self.window)
            if (
                not show_commit_info.panel_is_visible(self.window)
                and show_commit_info_panel
            ):
                self.window.run_command("show_panel", {"panel": "output.show_commit_info"})

            view.run_command("gs_log_graph_refresh")


class gs_graph_current_file(WindowCommand, GitCommand):
    def run(self, **kwargs):
        file_path = self.file_path
        if file_path:
            self.window.run_command("gs_graph", {"file_path": file_path, **kwargs})
        else:
            self.window.status_message("View has no filename to track.")


class gs_graph_current_path(WindowCommand, GitCommand):
    def run(self, **kwargs) -> None:
        if (
            (file_path := self.file_path)
            and (path := os.path.dirname(file_path))
        ):
            self.window.run_command("gs_graph", {"file_path": path, **kwargs})
        else:
            self.window.status_message("View has no path to track.")


class gs_graph_pickaxe(TextCommand, GitCommand):
    def run(self, edit):
        # type: (sublime.Edit) -> None
        view = self.view
        window = view.window()
        if not window:
            return
        repo_path = self.repo_path
        frozen_sel = list(view.sel())
        filters = " ".join(
            shlex.quote("-S{}".format(s))
            for r in frozen_sel
            if (s := view.substr(r))
            if (s.strip())
        )
        if not filters:
            flash(view, "Nothing selected.")
            return

        window.run_command("gs_graph", {"repo_path": repo_path, "filters": filters})


def augment_color_scheme(view):
    # type: (sublime.View) -> None
    settings = GitSavvySettings()
    colors = settings.get('colors').get('log_graph')
    if not colors:
        return

    themeGenerator = ThemeGenerator.for_view(view)
    themeGenerator.add_scoped_style(
        "GitSavvy Highlighted Commit Dot",
        DOT_SCOPE,
        background=colors['commit_dot_background'],
        foreground=colors['commit_dot_foreground'],
    )
    themeGenerator.add_scoped_style(
        "GitSavvy Highlighted Path Char",
        PATH_SCOPE,
        background=colors['path_background'],
        foreground=colors['path_foreground'],
    )
    themeGenerator.add_scoped_style(
        "GitSavvy Highlighted Commit Dot Above",
        DOT_ABOVE_SCOPE,
        background=colors['commit_dot_above_background'],
        foreground=colors['commit_dot_above_foreground'],
    )
    themeGenerator.add_scoped_style(
        "GitSavvy Highlighted Path Char Above",
        PATH_ABOVE_SCOPE,
        background=colors['path_above_background'],
        foreground=colors['path_above_foreground'],
    )
    themeGenerator.add_scoped_style(
        "GitSavvy Highlighted Matching Commit",
        MATCHING_COMMIT_SCOPE,
        background=colors['matching_commit_background'],
        foreground=colors['matching_commit_foreground'],
    )
    themeGenerator.add_scoped_style(
        "GitSavvy Multiselect Marker",
        multi_selector.MULTISELECT_SCOPE,
        background=colors['multiselect_foreground'],
        foreground=colors['multiselect_background'],
    )
    themeGenerator.apply_new_theme("log_graph_view", view)


class gs_log_graph_tab_out(GsTextCommand):
    def run(self, edit, reverse=False):
        options = self.current_state().get("default_graph_options", {})
        options.update({
            "all": self.view.settings().get("git_savvy.log_graph_view.all_branches")
        })
        self.update_store({"default_graph_options": options})
        self.view.settings().set("git_savvy.log_graph_view.default_graph", True)
        for view_ in self.window.views():
            if (
                view_ != self.view
                and (settings := view_.settings())
                and settings.get("git_savvy.log_graph_view.default_graph")
                and settings.get("git_savvy.repo_path") == self.repo_path
            ):
                settings.erase("git_savvy.log_graph_view.default_graph")

        self.view.run_command("gs_tab_cycle", {"source": "graph", "reverse": reverse})


class gs_log_graph_tab_in(WindowCommand, GitCommand):
    def run(self, file_path=None):
        for view in self.window.views():
            settings = view.settings()
            if (
                settings.get("git_savvy.log_graph_view.default_graph")
                and settings.get("git_savvy.repo_path") == self.repo_path
            ):
                focus_view(view)
                return

        all_branches = (
            self.current_state()
            .get("default_graph_options", {})
            .get("all", True)
        )
        self.window.run_command('gs_graph', {
            'file_path': file_path,
            'all': all_branches,
        })


class gs_log_graph(gs_log):
    """
    Defines the main menu if you invoke `git: graph` or `git: graph current file`.

    Accepts `current_file: bool` or `file_path: str` as (keyword) arguments, and
    ensures that each of the defined actions/commands in `default_actions` are finally
    called with `file_path` set.
    """
    default_actions = [
        ["gs_log_graph_current_branch", "For current branch"],
        ["gs_log_graph_all_branches", "For all branches"],
        ["gs_log_graph_by_branch", "For a specific branch..."],
        ["gs_log_graph_by_author", "Filtered by author..."],
    ]


class gs_log_graph_current_branch(WindowCommand, GitCommand):
    def run(self, file_path=None):
        branches_to_show = self.compute_branches_to_show("HEAD")
        self.window.run_command('gs_graph', {
            'file_path': file_path,
            'all': False,
            'branches': branches_to_show,
        })


class gs_log_graph_all_branches(WindowCommand, GitCommand):
    def run(self, file_path=None):
        self.window.run_command('gs_graph', {
            'file_path': file_path,
            'all': True,
        })


class gs_log_graph_by_author(WindowCommand, GitCommand):

    """
    Open a quick panel containing all committers for the active
    repository, ordered by most commits, Git name, and email.
    Once selected, display a quick panel with all commits made
    by the specified author.
    """

    def run(self, file_path=None):
        commiter_str = self.git("shortlog", "-sne", "HEAD")
        entries = []
        for line in commiter_str.split('\n'):
            m = re.search(r'\s*(\d*)\s*(.*)\s<(.*)>', line)
            if m is None:
                continue
            commit_count, author_name, author_email = m.groups()
            author_text = "{} <{}>".format(author_name, author_email)
            entries.append((commit_count, author_name, author_email, author_text))

        def on_select(index):
            selected_author = entries[index][3]
            self.window.run_command('gs_graph', {
                'file_path': file_path,
                'filters': f"--author='{selected_author}'"
            })

        email = self.git("config", "user.email").strip()
        show_quick_panel(
            self.window,
            [entry[3] for entry in entries],
            on_select,
            selected_index=[line[2] for line in entries].index(email)
        )


class gs_log_graph_by_branch(WindowCommand, GitCommand):
    """Open graph for a specific branch.

    Include the upstream of the branch (if any).  If no `branch`
    is given, ask for it.
    """
    _selected_branch = None

    def run(self, branch=None, file_path=None):
        def just_do_it(branch_):
            branches_to_show = self.compute_branches_to_show(branch_)
            self.window.run_command('gs_graph', {
                'file_path': file_path,
                'all': False,
                'branches': branches_to_show,
                'follow': branch_,
            })

        if branch:
            just_do_it(branch)

        else:
            def on_select(branch):
                self._selected_branch = branch  # remember last selection
                just_do_it(branch)

            show_branch_panel(on_select, selected_branch=self._selected_branch)


class gs_log_graph_navigate(TextCommand):
    def run(self, edit, forward=True, natural_movement=False):
        sel = self.view.sel()
        current_position = max(
            sel[0].a,
            # If inside the prelude section, jump to the *first*
            # commit.  For `.b`, Sublime already returns the first
            # row of the content section, thus `- 1` to compensate.
            find_by_selector(self.view, "meta.prelude")[0].b - 1
        )

        wanted_section = self.search(current_position, forward, natural_movement)
        if wanted_section is None:
            if natural_movement:
                self.view.run_command("move", {"by": "lines", "forward": forward})
            return

        sel.clear()
        sel.add(wanted_section.begin())
        show_region(self.view, wanted_section)

    def search(self, current_position, forwards=True, natural_movement=False):
        # type: (sublime.Point, bool, bool) -> Optional[sublime.Region]
        view = self.view
        row, col = view.rowcol(current_position)
        rows = count(row + 1, 1) if forwards else count(row - 1, -1)
        for row_ in rows:
            line = line_from_pt(view, view.text_point(row_, 0))
            if len(line) == 0:
                break

            commit_hash_region = extract_comit_hash_span(view, line)
            if not commit_hash_region:
                continue

            if not natural_movement:
                return commit_hash_region

            col_ = commit_hash_region.b - line.a
            if col <= col_:
                return commit_hash_region
            else:
                return sublime.Region(view.text_point(row_, col))
        return None


class gs_log_graph_navigate_wide(TextCommand):
    def run(self, edit, forward=True):
        # type: (sublime.Edit, bool) -> None
        view = self.view
        try:
            cur_dot = next(chain(find_graph_art_at_cursor(view), _find_dots(view)))
        except StopIteration:
            view.run_command("gs_log_graph_navigate", {"forward": forward})
            return

        # We actually want to intertwine edge commits and marked fixup commits
        # but I think that is difficult to do elegantly.  So I do it manually.
        marked_fixup_dots = [
            colorizer.Char(view, r.a) for r in view.get_regions('gs_log_graph_follow_fixups')
            if (r.a > cur_dot.pt if forward else r.a < cur_dot.pt)
        ]
        if marked_fixup_dots:
            next_fixup = marked_fixup_dots[0] if forward else marked_fixup_dots[-1]
        else:
            next_fixup = None

        next_dots = follow_first_parent(cur_dot, forward)
        try:
            next_dot = next(next_dots)
        except StopIteration:
            if next_fixup:
                next_dot = next_fixup
            else:
                return

        if line_distance(view, cur_dot.region(), next_dot.region()) < 2:
            # If the first next dot is not already a wide jump, t.i. the
            # cursor is not on an edge commit, follow the chain of consecutive
            # commits and select the last one of such a block.  T.i. select
            # the commit *before* the next wide jump.
            for next_dot, next_next_dot in pairwise(chain([next_dot], next_dots)):
                if line_distance(view, next_dot.region(), next_next_dot.region()) > 1:
                    break
            else:
                # If there is no wide jump anymore take the last found dot.
                # This is the case for example a the top of the graph, or if
                # a branch ends.
                # Catch if there is no next_next_dot (t.i. `next_dots` was empty),
                # then the last found dot is actually `next_dot`.
                try:
                    next_dot = next_next_dot
                except UnboundLocalError:
                    pass

        if next_fixup:
            next_dot = (min if forward else max)(next_dot, next_fixup, key=lambda dot: dot.pt)

        line = line_from_pt(view, next_dot.region())
        r = extract_comit_hash_span(view, line)
        if r:
            add_selection_to_jump_history(view)
            sel = view.sel()
            sel.clear()
            sel.add(r.begin())
            show_region(
                view,
                join_regions(cur_dot.region(), r),
                prefer_end=True if forward else False
            )


def follow_first_parent(dot, forward=True):
    # type: (colorizer.Char, bool) -> Iterator[colorizer.Char]
    """Follow left (first-parent) dot to dot omitting the path chars in between."""
    while True:
        try:
            dot = next(dots_after_dot(dot, forward))
        except StopIteration:
            return
        else:
            yield dot


def follow_dots(dot, forward=True):
    # type: (colorizer.Char, bool) -> Iterator[colorizer.Char]
    """Breadth first traverse dot to dot."""
    # Always sort by `dot.pt` to keep the order exactly like the visual
    # order in the view.
    stack = sorted(dots_after_dot(dot, forward), key=lambda dot: dot.pt, reverse=not forward)
    seen = set()
    while stack:
        dot = stack.pop(0)
        if dot not in seen:
            yield dot
            seen.add(dot)
            stack.extend(dots_after_dot(dot, forward))
            stack.sort(key=lambda dot: dot.pt, reverse=not forward)


def dots_after_dot(dot, forward=True):
    # type: (colorizer.Char, bool) -> Iterator[colorizer.Char]
    """Return exact next dots (commits) after `dot`."""
    fn = colorizer.follow_path_down if forward else colorizer.follow_path_up
    return filter(lambda ch: ch.char() in COMMIT_NODE_CHARS, fn(dot))


class gs_log_graph_navigate_to_head(TextCommand):

    """
    Travel to the HEAD commit.
    """

    def run(self, edit):
        try:
            head_commit = self.view.find_by_selector(
                "git-savvy.graph meta.graph.graph-line.head.git-savvy "
                "constant.numeric.graph.commit-hash.git-savvy"
            )[0]
        except IndexError:
            settings = self.view.settings()
            settings.set("git_savvy.log_graph_view.all_branches", True)
            settings.set("git_savvy.log_graph_view.follow", "HEAD")
            self.view.run_command("gs_log_graph_refresh")
        else:
            set_and_show_cursor(self.view, head_commit.begin())


class gs_log_graph_edit_branches(TextCommand):
    def run(self, edit):
        settings = self.view.settings()
        branches = settings.get("git_savvy.log_graph_view.branches", [])  # type: List[str]

        def on_done(text):
            # type: (str) -> None
            new_branches = list(filter_(text.split(' ')))
            settings.set("git_savvy.log_graph_view.branches", new_branches)
            self.view.run_command("gs_log_graph_refresh")

        show_single_line_input_panel(
            "branches", ' '.join(branches), on_done, select_text=True
        )


DEFAULT_HISTORY_ENTRIES = ["--date-order", "--dense", "--first-parent", "--reflog"]


class gs_log_graph_edit_filters(TextCommand):
    def run(self, edit):
        # type: (sublime.Edit) -> None
        view = self.view
        settings = view.settings()
        applying_filters = settings.get("git_savvy.log_graph_view.apply_filters")
        filters = (
            settings.get("git_savvy.log_graph_view.filters", "")
            if applying_filters
            else ""
        )
        filter_history = settings.get("git_savvy.log_graph_view.filter_history")
        if not filter_history:
            filter_history = DEFAULT_HISTORY_ENTRIES + ([filters] if filters else [])

        author_tip = self.get_author_tip()
        history_entries = (
            (
                [author_tip]
                if author_tip and author_tip not in filter_history
                else []
            )
            + filter_history
        )
        virtual_entries_count = len(history_entries) - len(filter_history)
        active = index_of(history_entries, filters, -1)

        def on_done(text):
            # type: (str) -> None
            if not text:
                # A user can delete entries from the history by selecting
                # an entry, deleting the contents, and finally hitting `enter`.
                # Note that `active` can be "-1" (often the default), denoting
                # the imaginary empty last entry. It is also offset since we may
                # have added "virtual" entries (the `author_tip`) at the top of it
                # which cannot be deleted, just like the `DEFAULT_HISTORY_ENTRIES`.
                new_active = (
                    input_panel_settings.get("input_panel_with_history.active")
                    - virtual_entries_count
                )
                if 0 <= new_active < len(filter_history):
                    if filter_history[new_active] not in DEFAULT_HISTORY_ENTRIES:
                        filter_history.pop(new_active)

            new_filter_history = (
                filter_history
                if text in filter_history or not text
                else (filter_history + [text])
            )
            settings.set("git_savvy.log_graph_view.apply_filters", True)
            settings.set("git_savvy.log_graph_view.filters", text)
            settings.set("git_savvy.log_graph_view.filter_history", new_filter_history)
            if not applying_filters:
                settings.set("git_savvy.log_graph_view.paths", [])

            hide_toast()
            view.run_command("gs_log_graph_refresh", {"assume_complete_redraw": bool(text)})

        def on_cancel():
            enqueue_on_worker(hide_toast)

        input_panel = show_single_line_input_panel(
            "additional args",
            filters,
            on_done,
            on_cancel=on_cancel,
            select_text=True
        )
        input_panel_settings = input_panel.settings()
        input_panel_settings.set("input_panel_with_history", True)
        input_panel_settings.set("input_panel_with_history.entries", history_entries)
        input_panel_settings.set("input_panel_with_history.active", active)

        hide_toast = show_toast(
            view,
            "↑↓ for the history\n"
            "Examples:  -Ssearch_term  |  -Gsearch_term  ",
            timeout=-1
        )

    def get_author_tip(self):
        # type: () -> str
        view = self.view
        line_span = view.line(view.sel()[0].b)
        for r in find_by_selector(view, "entity.name.tag.author.git-savvy"):
            if line_span.intersects(r):
                return "--author='{}' -i".format(view.substr(r).strip())
        return ""


def index_of(seq, needle, default):
    # type: (Sequence[T], T, int) -> int
    try:
        return seq.index(needle)
    except ValueError:
        return default


class gs_input_handler_go_history(TextCommand):
    def run(self, edit, forward=True):
        # type: (sublime.Edit, bool) -> None
        # In the case of an input handler, `self.view.settings` is cached
        # and returns stale answers.  We work-around by recreating the
        # `view` object which in turn recreates the `settings` object freshly.
        view = sublime.View(self.view.id())
        settings = view.settings()
        history = settings.get("input_panel_with_history.entries")
        if not history:
            return

        len_history = len(history)
        active = settings.get("input_panel_with_history.active", -1)
        if active == -1:
            active = len_history

        if forward:
            active += 1
        else:
            active -= 1

        active = max(0, min(len_history, active))
        text = history[active] if active < len_history else ""
        replace_view_content(view, text)
        view.run_command("move_to", {"to": "eol", "extend": False})
        view.settings().set("input_panel_with_history.active", active)

        show_toast(
            view,
            "\n".join(
                "   {}".format(entry) if idx != active else ">  {}".format(entry)
                for idx, entry in enumerate(history + [""])
            ),
            timeout=2500
        )


class gs_log_graph_reset_filters(TextCommand):
    def run(self, edit):
        settings = self.view.settings()
        current = settings.get("git_savvy.log_graph_view.apply_filters")
        next_state = not current
        settings.set("git_savvy.log_graph_view.apply_filters", next_state)
        self.view.run_command("gs_log_graph_refresh", {"assume_complete_redraw": next_state})


class gs_log_graph_toggle_overview(TextCommand):
    def run(self, edit):
        view = self.view
        settings = view.settings()
        current = settings.get("git_savvy.log_graph_view.overview")
        next_state = not current
        if next_state:
            follow = settings.get("git_savvy.log_graph_view.follow")
            symbols = view.symbols()
            symbols_ = {s for _, s in symbols}
            if follow not in symbols_:
                dots = find_dots(view)
                if len(dots) == 1:
                    dot = dots.pop()
                    s = next_symbol_upwards(view, symbols, dot)
                    settings.set("git_savvy.log_graph_view.follow", s)

        settings.set("git_savvy.log_graph_view.overview", next_state)
        self.view.run_command("gs_log_graph_refresh", {"assume_complete_redraw": True})


def next_symbol_upwards(view, symbols, dot):
    # type: (sublime.View, List[Tuple[sublime.Region, str]], colorizer.Char) -> Optional[str]
    previous_dots = follow_dots(dot, forward=False)
    for dot in take(50, previous_dots):
        line_span = view.line(dot.pt)
        # Capture all symbols from the line, ...
        symbols_on_line = []
        for r, s in symbols:
            if line_span.a <= r.a <= line_span.b:
                symbols_on_line.append(s)
            if r.a > line_span.b:
                break
        # ... and choose the last one as git puts remote branches first.
        if symbols_on_line:
            return symbols_on_line[-1]
    return None


class gs_log_graph_edit_files(TextCommand, GitCommand):
    def run(self, edit, selected_index=-1):
        view = self.view
        settings = view.settings()
        window = view.window()
        assert window

        files = self.list_controlled_files(view.change_count())
        apply_filters = settings.get("git_savvy.log_graph_view.apply_filters")
        paths = (
            settings.get("git_savvy.log_graph_view.paths", [])
            if apply_filters
            else []
        )  # type: List[str]
        items = (
            [
                ">  {}".format(file)
                for file in paths
            ]
            +
            sorted(
                "   {}".format(file)
                for file in chain(files, set(os.path.dirname(f) for f in files))
                if file and file not in paths
            )
        )

        def on_done(idx, event={}):
            selected = items[idx]
            unselect = selected[0] == ">"
            path = selected[3:]
            if unselect:
                next_paths = [p for p in paths if p != path]
            else:
                next_paths = paths + [path]

            settings.set("git_savvy.log_graph_view.paths", next_paths)
            settings.set("git_savvy.log_graph_view.apply_filters", True)
            if not apply_filters:
                settings.set("git_savvy.log_graph_view.filters", "")
            view.run_command("gs_log_graph_refresh", {"assume_complete_redraw": bool(next_paths)})
            if event.get("modifier_keys", {}).get("primary"):
                view.run_command("gs_log_graph_edit_files", {
                    "selected_index": max(0, idx - 1) if unselect else next_paths.index(path)
                })

        show_quick_panel(
            window,
            items,
            on_done,
            flags=sublime.MONOSPACE_FONT | (
                sublime.WANT_EVENT if QUICK_PANEL_SUPPORTS_WANT_EVENT else 0
            ),
            selected_index=selected_index
        )

    @lru_cache(1)
    def list_controlled_files(self, __cc):
        # type: (int) -> List[str]
        return self.git(
            "ls-tree",
            "-r",
            "--full-tree",
            "--name-only",
            "HEAD"
        ).strip().splitlines()


class gs_log_graph_toggle_all_setting(TextCommand, GitCommand):
    def run(self, edit):
        settings = self.view.settings()
        overview = settings.get("git_savvy.log_graph_view.overview")
        setting_name = "show_tags" if overview else "all_branches"
        current = settings.get("git_savvy.log_graph_view.{}".format(setting_name))
        next_state = not current
        settings.set("git_savvy.log_graph_view.{}".format(setting_name), next_state)
        self.view.run_command("gs_log_graph_refresh")


class gs_log_graph_open_commit(TextCommand):
    def run(self, edit):
        # type: (...) -> None
        window = self.view.window()
        if not window:
            return

        sel = get_simple_selection(self.view)
        if sel is None:
            return
        line = line_from_pt(self.view, sel)
        commit_hash = extract_commit_hash(line.text)
        if not commit_hash:
            return

        window.run_command("gs_show_commit", {"commit_hash": commit_hash})


PANEL_JUST_LOST_FOCUS = False


class GsLogGraphCursorListener(EventListener, GitCommand):
    def is_applicable(self, view):
        # type: (sublime.View) -> bool
        return bool(view.settings().get("git_savvy.log_graph_view"))

    def on_deactivated(self, view):
        # type: (sublime.View) -> None
        global PANEL_JUST_LOST_FOCUS
        window = view.window()
        if not window:
            return
        panel_view = window.find_output_panel('show_commit_info')
        PANEL_JUST_LOST_FOCUS = bool(panel_view and panel_view.id() == view.id())

    def on_activated(self, view):
        # type: (sublime.View) -> None
        window = view.window()
        if not window:
            return

        if view not in window.views():
            return

        if self.is_applicable(view):
            show_commit_info.ensure_panel(window)

        panel_view = window.find_output_panel('show_commit_info')
        if not panel_view:
            return

        # Do nothing, if the user focuses the panel
        if panel_view.id() == view.id():
            return

        # Auto-hide panel if the user switches to a different buffer
        if (
            not self.is_applicable(view)
            and show_commit_info.panel_is_visible(window)
            and show_commit_info.panel_belongs_to_graph(panel_view)
        ):
            if PANEL_JUST_LOST_FOCUS:
                panel_view.settings().set("git_savvy.show_commit_view.had_focus", True)

            panel = PREVIOUS_OPEN_PANEL_PER_WINDOW.get(window.id(), None)
            if panel:
                window.run_command("show_panel", {"panel": panel})
            else:
                window.run_command("hide_panel")

        # Auto-show panel if the user switches back
        elif (
            self.is_applicable(view)
            and not show_commit_info.panel_is_visible(window)
            and view.settings().get("git_savvy.log_graph_view.show_commit_info_panel")
        ):
            window.run_command("show_panel", {"panel": "output.show_commit_info"})
            if panel_view.settings().get("git_savvy.show_commit_view.had_focus"):
                # At this point `active_panel()` is already "show_commit_info"`
                # but still it can't receive focus before the next tick.
                # :shrug: as I could not find a reason or work-around.
                enqueue_on_ui(window.focus_view, panel_view)
                panel_view.settings().set("git_savvy.show_commit_view.had_focus", False)

    # `on_selection_modified` triggers twice per mouse click
    # multiplied with the number of views into the same buffer,
    # hence it is *important* to throttle these events.
    # We do this separately per side-effect. See the fn
    # implementations.
    def on_selection_modified(self, view):
        # type: (sublime.View) -> None
        if not self.is_applicable(view):
            return

        window = view.window()
        if window and show_commit_info.panel_is_visible(window):
            draw_info_panel(view)

        # `colorize_dots` queries the view heavily. We want that to
        # happen on the main thread (t.i. blocking) bc it is way, way
        # faster. But we still defer that task, so others can run code
        # that actually *needs* to be a sync side-effect to this event.
        enqueue_on_ui(colorize_dots, view)
        enqueue_on_ui(colorize_fixups, view)

        enqueue_on_ui(set_symbol_to_follow, view)

    def on_window_command(self, window, command_name, args):
        # type: (sublime.Window, str, Dict) -> None
        if command_name == 'hide_panel':
            view = window.active_view()
            if not view:
                return

            if window.active_panel() == "incremental_find":
                return

            # If the user hides the panel via `<ESC>` or mouse click,
            # remember the intent *only if* the `active_view` is a 'log_graph'
            if self.is_applicable(view):
                remember_commit_panel_state(view, False)
            PREVIOUS_OPEN_PANEL_PER_WINDOW[window.id()] = None

        elif command_name == 'show_panel':
            view = window.active_view()
            if not view:
                return

            # Special case some panels. For these panels, showing them does not count
            # as intent to close the show_commit panel. It will thus reappear
            # automatically as soon as you focus the graph again. E.g. closing the
            # incremental find panel via `<enter>` will bring the commit panel up
            # again.
            if args.get('panel') == "incremental_find":
                return

            toggle = args.get('toggle', False)
            panel = args.get('panel')
            if toggle and window.active_panel() == panel:  # <== actually *hide* panel
                # E.g. the same side-effect as in above "hide_panel" case
                if self.is_applicable(view):
                    remember_commit_panel_state(view, False)
                PREVIOUS_OPEN_PANEL_PER_WINDOW[window.id()] = None
            else:
                if panel == "output.show_commit_info":
                    if self.is_applicable(view):
                        remember_commit_panel_state(view, True)
                    PREVIOUS_OPEN_PANEL_PER_WINDOW[window.id()] = window.active_panel()
                    draw_info_panel(view)
                else:
                    if self.is_applicable(view):
                        remember_commit_panel_state(view, False)


PREVIOUS_OPEN_PANEL_PER_WINDOW = {}  # type: Dict[sublime.WindowId, Optional[str]]


def remember_commit_panel_state(view, state):
    # type: (sublime.View, bool) -> None
    # Note `view` is the ("parent") log graph view!
    view.settings().set("git_savvy.log_graph_view.show_commit_info_panel", state)
    # Also save to global state as the new initial mode
    # for the next graph view.
    GitSavvySettings().set("graph_show_more_commit_info", state)


def set_symbol_to_follow(view):
    # type: (sublime.View) -> None

    # `on_selection_modified` is called *often* while we render, just as
    # its by-product and without any intent of the user.
    # This is especially problematic if the cursor is on a line we don't
    # `follow` as we might overwrite `follow` and call `gs_log_graph_refresh`
    # again.
    # Filter early by checking the current `line.text`.  This is obviously
    # also okay and efficient when the user moves the cursor just left and right.
    # Note that we don't block *all* events here during "render" as that would
    # filter out intentional changes as well.  But we want that an intentional
    # move aborts the current render and restarts.  This is an important feature
    # for very long graphs.

    try:
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return

    line = line_from_pt(view, cursor)
    _set_symbol_to_follow(view, line.text)


@lru_cache(1)
def _set_symbol_to_follow(view: sublime.View, line_text: str) -> None:
    symbol = _extract_symbol_to_follow(view, line_text)
    if not symbol:
        return
    previous_value = view.settings().get('git_savvy.log_graph_view.follow')
    if symbol != previous_value:
        view.settings().set('git_savvy.log_graph_view.follow', symbol)

        # Check if the view endswith our `continuation_marker` and decide if we
        # need to expand or shrink the graph.
        continuation_marker = "...\n"
        view_size = view.size()
        continuation_line = sublime.Region(view_size - len(continuation_marker), view_size)
        if view.substr(continuation_line) == continuation_marker:
            try:
                cursor = [s.b for s in view.sel()][-1]
            except IndexError:
                return

            max_row, _ = view.rowcol(continuation_line.a)
            cur_row, _ = view.rowcol(cursor)
            if not (GRAPH_HEIGHT * 0.5 < max_row - cur_row < GRAPH_HEIGHT * 2):
                view.run_command("gs_log_graph_refresh")


def extract_symbol_to_follow(view):
    # type: (sublime.View) -> Optional[str]
    """Extract a symbol to follow."""
    try:
        # Intentional `b` (not `end()`!) because b is where the
        # cursor is. (If you select upwards b becomes < a.)
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return None

    line = line_from_pt(view, cursor)
    return _extract_symbol_to_follow(view, line.text)


@lru_cache(maxsize=512)
def _extract_symbol_to_follow(view, line_text):
    # type: (sublime.View, str) -> Optional[str]
    try:
        # Intentional `b` (not `end()`!) because b is where the
        # cursor is. (If you select upwards b becomes < a.)
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return None

    if view.match_selector(cursor, 'meta.graph.graph-line.head.git-savvy'):
        return 'HEAD'

    line_span = view.line(cursor)
    symbols_on_line = [
        symbol
        for r, symbol in view.symbols()
        if line_span.contains(r)
    ]
    if symbols_on_line:
        # git always puts the remotes first so we take
        # the last one which is (then) a local branch.
        return symbols_on_line[-1]

    return extract_commit_hash(line_text)


def navigate_to_symbol(
    view,            # type: sublime.View
    symbol,          # type: str
    if_before=None,  # type: sublime.Region
    col_range=None,  # type: Tuple[int, int]
    method=None,     # type: Callable[[sublime.View, Union[sublime.Region, sublime.Point]], None]
):
    # type: (...) -> bool
    jump_position = jump_position_for_symbol(view, symbol, if_before, col_range)
    if jump_position is None:
        return False

    if method is None:
        method = set_and_show_cursor
    method(view, jump_position)
    return True


def jump_position_for_symbol(
    view,            # type: sublime.View
    symbol,          # type: str
    if_before=None,  # type: Optional[sublime.Region]
    col_range=None   # type: Optional[Tuple[int, int]]
):
    # type: (...) -> Optional[Union[sublime.Region, sublime.Point]]
    region = _find_symbol(view, symbol)
    if region is None:  # explicit `None` checks bc empty regions are falsy!
        return None
    if if_before is not None and region.end() > if_before.end():
        return None

    if col_range is None:
        return region.begin()

    line_start = line_start_of_region(view, region)
    wanted_region = Region(*col_range) + line_start
    # Normalize single cursors *before* the commit hash
    # (t.i. `region.end()`) to `region.begin()`.
    if wanted_region.empty() and wanted_region.a < region.end():
        return region.begin()
    else:
        return wanted_region


def _find_symbol(view, symbol):
    # type: (sublime.View, str) -> Optional[sublime.Region]
    if symbol == 'HEAD':
        try:
            return view.find_by_selector(
                'meta.graph.graph-line.head.git-savvy '
                'constant.numeric.graph.commit-hash.git-savvy'
            )[0]
        except IndexError:
            return None

    for r, symbol_ in view.symbols():
        if symbol_ == symbol:
            line = line_from_pt(view, r)
            return extract_comit_hash_span(view, line)

    r = view.find(FIND_COMMIT_HASH + re.escape(symbol), 0)
    if not r.empty():
        line = line_from_pt(view, r)
        return extract_comit_hash_span(view, line)
    return None


def extract_comit_hash_span(view, line):
    # type: (sublime.View, TextRange) -> Optional[sublime.Region]
    match = COMMIT_LINE.search(line.text)
    if match:
        a, b = match.span('commit_hash')
        return sublime.Region(a + line.a, b + line.a)
    return None


@text_command
def set_and_show_cursor(view, point_or_region):
    # type: (sublime.View, Union[sublime.Region, sublime.Point]) -> None
    just_set_cursor(view, point_or_region)
    view.show(view.sel(), True)


@text_command
def just_set_cursor(view, point_or_region):
    # type: (sublime.View, Union[sublime.Region, sublime.Point]) -> None
    sel = view.sel()
    sel.clear()
    sel.add(point_or_region)


def get_simple_selection(view):
    # type: (sublime.View) -> Optional[sublime.Region]
    sel = [s for s in view.sel()]
    if len(sel) != 1 or len(view.lines(sel[0])) != 1:
        return None

    return sel[0]


def line_start_of_region(view, region):
    # type: (sublime.View, sublime.Region) -> sublime.Point
    return view.line(region).begin()


def colorize_dots(view):
    # type: (sublime.View) -> None
    dots = find_graph_art_at_cursor(view) or tuple(find_dots(view))
    _colorize_dots(view.id(), dots)


def find_graph_art_at_cursor(view):
    # type: (sublime.View) -> Tuple[colorizer.Char, ...]
    if len(view.sel()) != 1:
        return ()
    cursor = view.sel()[0].b
    if not view.match_selector(cursor, "meta.graph.branch-art"):
        return ()

    c = colorizer.Char(view, cursor)
    for get_char in (lambda: c, lambda: c.w):
        c_ = get_char()
        if c_ != " ":
            return (c_,)
    return ()


def find_dots(view):
    # type: (sublime.View) -> Set[colorizer.Char]
    return set(_find_dots(view))


def _find_dots(view):
    # type: (sublime.View) -> Iterator[colorizer.Char]
    for s in view.sel():
        line = line_from_pt(view, s.begin())
        dot = dot_from_line(view, line)
        if dot:
            yield dot


def line_from_pt(view, pt):
    # type: (sublime.View, Union[sublime.Point, sublime.Region]) -> TextRange
    line_span = view.line(pt)
    line_text = view.substr(line_span)
    return TextRange(line_text, line_span.a, line_span.b)


def dot_from_line(view, line):
    # type: (sublime.View, TextRange) -> Optional[colorizer.Char]
    for ch in COMMIT_NODE_CHARS:
        idx = line.text.find(ch)
        if idx > -1:
            return colorizer.Char(view, line.region().begin() + idx)
    return None


ACTIVE_COMPUTATION = Cache()


@lru_cache(maxsize=1)
# ^- throttle side-effects
def _colorize_dots(vid, dots):
    # type: (sublime.ViewId, Tuple[colorizer.Char]) -> None
    view = sublime.View(vid)
    to_region = lambda ch: ch.region()  # type: Callable[[colorizer.Char], sublime.Region]

    view.add_regions(
        'gs_log_graph.dot',
        list(map(to_region, dots)),
        scope=DOT_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )

    ACTIVE_COMPUTATION[vid] = dots
    __colorize_dots(vid, dots)


@cooperative_thread_hopper
def __colorize_dots(vid, dots):
    # type: (sublime.ViewId, Tuple[colorizer.Char]) -> HopperR
    timer = yield "ENSURE_UI_THREAD"
    view = sublime.View(vid)

    paths_down = []  # type: List[List[colorizer.Char]]
    paths_up = []  # type: List[List[colorizer.Char]]

    uow = []
    for container, direction in ((paths_down, "down"), (paths_up, "up")):
        for dot in dots:
            try:
                chars = colorizer.follow_path_if_cached(dot, direction)  # type: ignore[arg-type]
            except ValueError:
                values = []  # type: List[colorizer.Char]
                container.append(values)
                uow.append((colorizer.follow_path(dot, direction), values))  # type: ignore[arg-type]
            else:
                container.append(chars)

    c = 0
    while uow:
        idx = c % len(uow)
        iterator, values = uow[idx]
        try:
            char = next(iterator)
        except StopIteration:
            uow.pop(idx)
        else:
            values.append(char)
        c += 1

        if timer.exhausted_ui_budget():
            __paint(view, paths_down, paths_up)
            timer = yield "AWAIT_UI_THREAD"
            if ACTIVE_COMPUTATION.get(vid) != dots:
                return

    if ACTIVE_COMPUTATION[vid] == dots:
        ACTIVE_COMPUTATION.pop(vid, None)
        __paint(view, paths_down, paths_up)


def __paint(view, paths_down, paths_up):
    # type: (sublime.View, List[List[colorizer.Char]], List[List[colorizer.Char]]) -> None
    to_region = lambda ch: ch.region()  # type: Callable[[colorizer.Char], sublime.Region]
    path_down = flatten(filter(
        lambda path: len(path) > 1,  # type: ignore[arg-type]  # https://github.com/python/mypy/issues/9176
        paths_down
    ))
    chars_up = flatten(filter(
        lambda path: len(path) > 1,  # type: ignore[arg-type]  # https://github.com/python/mypy/issues/9176
        paths_up
    ))
    path_up, dot_up = partition(lambda ch: ch.char() in COMMIT_NODE_CHARS, chars_up)
    view.add_regions(
        'gs_log_graph.path_below',
        list(map(to_region, path_down)),
        scope=PATH_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )
    view.add_regions(
        'gs_log_graph.path_above',
        list(map(to_region, path_up)),
        scope=PATH_ABOVE_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )
    view.add_regions(
        'gs_log_graph.dot.above',
        list(map(to_region, dot_up)),
        scope=DOT_ABOVE_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )


def colorize_fixups(view):
    # type: (sublime.View) -> None
    dots = tuple(find_dots(view))
    _colorize_fixups(view.id(), dots)


@lru_cache(maxsize=1)
def _colorize_fixups(vid, dots):
    # type: (sublime.ViewId, Tuple[colorizer.Char]) -> None
    view = sublime.View(vid)

    matching_dots = flatten(__find_matching_dots(vid, dot) for dot in dots)
    view.add_regions(
        'gs_log_graph_follow_fixups',
        [dot.region() for dot in matching_dots],
        scope=MATCHING_COMMIT_SCOPE,
        flags=sublime.RegionFlags.NO_UNDO
    )


@lru_cache(maxsize=64)  # If we cache, we must return non-lazy `List`!
def __find_matching_dots(vid, dot):
    # type: (sublime.ViewId, colorizer.Char) -> List[colorizer.Char]
    view = sublime.View(vid)
    commit_message = commit_message_from_point(view, dot.pt)
    if not commit_message:
        return []

    if is_fixup_or_squash_message(commit_message):
        original_message = strip_fixup_or_squash_prefix(commit_message)
        return take(1, find_matching_commit(dot, original_message))
    else:
        return list(dot for dot, _ in find_fixups_upwards(dot, commit_message))


def extract_message_regions(view):
    # type: (sublime.View) -> List[sublime.Region]
    return find_by_selector(view, "meta.graph.message.git-savvy")


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


def add_fixup_or_squash_prefixes(commit_message):
    # type: (str) -> List[str]
    return [
        "fixup! " + commit_message,
        "squash! " + commit_message
    ]


def commit_message_from_point(view, pt):
    # type: (sublime.View, int) -> Optional[str]
    line_span = view.line(pt)
    for r in extract_message_regions(view):
        if line_span.a <= r.a <= line_span.b:  # optimized `line_span.contains(r)`
            return view.substr(r)
    else:
        return None


def find_matching_commit(dot, message, forward=True):
    # type: (colorizer.Char, str, bool) -> Iterator[colorizer.Char]
    dots = follow_dots(dot, forward=forward)
    for dot, this_message in _with_message(take(100, dots)):
        this_message = this_message.rstrip(".").strip()
        shorter, longer = sorted((message, this_message), key=len)
        if longer.startswith(shorter):
            yield dot


def find_fixups_upwards(dot, message):
    # type: (colorizer.Char, str) -> Iterator[Tuple[colorizer.Char, str]]
    messages = add_fixup_or_squash_prefixes(message.rstrip(".").strip())

    previous_dots = follow_dots(dot, forward=False)
    for dot, this_message in _with_message(take(50, previous_dots)):
        this_message = this_message.rstrip(".").strip()
        if is_fixup_or_squash_message(this_message):
            for message in messages:
                shorter, longer = sorted((message, this_message), key=len)
                if longer.startswith(shorter):
                    yield dot, this_message


def _with_message(dots):
    # type: (Iterable[colorizer.Char]) -> Iterator[Tuple[colorizer.Char, str]]
    return filter_(map(with_message, dots))


def with_message(dot):
    # type: (colorizer.Char) -> Optional[Tuple[colorizer.Char, str]]
    commit_message = commit_message_from_point(dot.view, dot.pt)
    if commit_message:
        return dot, commit_message
    return None


def draw_info_panel(view):
    # type: (sublime.View) -> None
    """Extract line under the last cursor and draw info panel."""
    try:
        # Intentional `b` (not `end()`!) because b is where the
        # cursor is. (If you select upwards b becomes < a.)
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return

    line = line_from_pt(view, cursor)
    draw_info_panel_for_line(view.id(), line.text)


@lru_cache(maxsize=1)
# ^- used to throttle the side-effect!
# Read: distinct until      (vid, line_text) changes
def draw_info_panel_for_line(vid, line_text):
    # type: (sublime.ViewId, str) -> None
    view = sublime.View(vid)
    window = view.window()
    if not window:
        return

    commit_hash = extract_commit_hash(line_text)
    # `gs_show_commit_info` draws a blank panel if `commit_hash`
    # is falsy.  That only looks nice iff the main graph view is
    # also blank. (Which it only ever is directly after creation.)
    # If you just move the cursor to a line not containing a
    # commit_hash, it looks better to not draw at all, t.i. the
    # information in the panel stays untouched.
    if view.size() == 0 or commit_hash:
        window.run_command("gs_show_commit_info", {
            "commit_hash": commit_hash,
            "from_log_graph": True
        })


def extract_commit_hash(line):
    # type: (str) -> str
    match = COMMIT_LINE.search(line)
    return match.group('commit_hash') if match else ""


class gs_log_graph_toggle_commit_info_panel(WindowCommand, GitCommand):
    """ Toggle commit info output panel."""
    def run(self):
        if show_commit_info.panel_is_visible(self.window):
            self.window.run_command("hide_panel", {"panel": "output.show_commit_info"})
        else:
            self.window.run_command("show_panel", {"panel": "output.show_commit_info"})


class gs_log_graph_show_and_focus_panel(WindowCommand, GitCommand):
    def run(self, panel: str) -> None:
        if self.window.active_panel() != f"output.{panel}":
            self.window.run_command("show_panel", {"panel": f"output.{panel}"})

        panel_view = self.window.find_output_panel(panel)
        if not panel_view:
            return

        self.window.focus_view(panel_view)
