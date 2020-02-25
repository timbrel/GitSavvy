from functools import lru_cache, partial
from itertools import islice
import os
import re

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from . import log_graph_colorizer as colorizer, show_commit_info
from .log import GsLogCommand
from .navigate import GsNavigate
from ..fns import filter_
from ..git_command import GitCommand, GitSavvyError
from ..parse_diff import Region
from ..settings import GitSavvySettings
from ..runtime import enqueue_on_ui, enqueue_on_worker, run_on_new_thread, text_command
from ..ui_mixins.quick_panel import show_branch_panel
from ...common import util
from ...common.theme_generator import XMLThemeGenerator, JSONThemeGenerator


MYPY = False
if MYPY:
    from typing import Callable, Dict, Iterable, Iterator, List, Optional, Set, Tuple


COMMIT_NODE_CHAR = "●"
COMMIT_NODE_CHAR_OPTIONS = "●*"
GRAPH_CHAR_OPTIONS = r" /_\|\-\\."
COMMIT_LINE = re.compile(
    r"^[{graph_chars}]*[{node_chars}][{graph_chars}]* "
    r"(?P<commit_hash>[a-f0-9]{{5,40}}) "
    r"(?P<decoration>\(.+?\))?"
    .format(graph_chars=GRAPH_CHAR_OPTIONS, node_chars=COMMIT_NODE_CHAR_OPTIONS)
)

DOT_SCOPE = 'git_savvy.graph.dot'
PATH_SCOPE = 'git_savvy.graph.path_char'
MATCHING_COMMIT_SCOPE = 'git_savvy.graph.matching_commit'


def compute_identifier_for_view(view):
    # type: (sublime.View) -> Optional[Tuple]
    settings = view.settings()
    return (
        settings.get('git_savvy.repo_path'),
        settings.get('git_savvy.file_path'),
        settings.get('git_savvy.log_graph_view.all_branches')
        or settings.get('git_savvy.log_graph_view.branches')
    ) if settings.get('git_savvy.log_graph_view') else None


def focus_view(view):
    window = view.window()
    if not window:
        return

    group, _ = window.get_view_index(view)
    window.focus_group(group)
    window.focus_view(view)


class GsGraphCommand(WindowCommand, GitCommand):
    def run(
        self,
        repo_path=None,
        file_path=None,
        all=False,
        branches=None,
        author='',
        title='GRAPH',
        follow=None
    ):
        if repo_path is None:
            repo_path = self.repo_path
        assert repo_path

        this_id = (
            repo_path,
            file_path,
            all or branches
        )
        for view in self.window.views():
            if compute_identifier_for_view(view) == this_id:
                settings = view.settings()
                settings.set("git_savvy.log_graph_view.all_branches", all)
                settings.set("git_savvy.log_graph_view.filter_by_author", author)
                settings.set("git_savvy.log_graph_view.branches", branches or [])
                settings.set('git_savvy.log_graph_view.follow', follow)

                if follow and follow != extract_symbol_to_follow(view):
                    if show_commit_info.panel_is_visible(self.window):
                        # Hack to force a synchronous update of the panel
                        # *as a result of* `navigate_to_symbol` (by
                        # `on_selection_modified`) since we know that
                        # "show_commit_info" will run blocking if the panel
                        # is empty (or closed).
                        panel = show_commit_info.ensure_panel(self.window)
                        panel.run_command(
                            "gs_replace_view_text", {"text": "", "restore_cursors": True}
                        )
                    navigate_to_symbol(view, follow)

                focus_view(view)
                break
        else:
            view = util.view.get_scratch_view(self, "log_graph", read_only=True)
            view.set_syntax_file("Packages/GitSavvy/syntax/graph.sublime-syntax")
            view.run_command("gs_handle_vintageous")
            view.run_command("gs_handle_arrow_keys")
            run_on_new_thread(augment_color_scheme, view)

            settings = view.settings()
            settings.set("git_savvy.repo_path", repo_path)
            settings.set("git_savvy.file_path", file_path)
            settings.set("git_savvy.log_graph_view.all_branches", all)
            settings.set("git_savvy.log_graph_view.filter_by_author", author)
            settings.set("git_savvy.log_graph_view.branches", branches or [])
            settings.set('git_savvy.log_graph_view.follow', follow)
            view.set_name(title)

            # We need to ensure the panel has been created, so it appears
            # e.g. in the menu. Otherwise Sublime will not handle `show_panel`
            # events for that panel at all.
            # Note that the following is basically what `on_activated` does,
            # but `on_activated` runs synchronous when a view gets created t.i.
            # even before we can mark it as "graph_view" in the settings.
            show_commit_info.ensure_panel(self.window)
            if (
                self.savvy_settings.get("graph_show_more_commit_info")
                and not show_commit_info.panel_is_visible(self.window)
            ):
                self.window.run_command("show_panel", {"panel": "output.show_commit_info"})

            view.run_command("gs_log_graph_refresh", {"navigate_after_draw": True})


class GsGraphCurrentFile(WindowCommand, GitCommand):
    def run(self, **kwargs):
        file_path = self.file_path
        if file_path:
            self.window.run_command('gs_graph', dict(file_path=file_path, **kwargs))
        else:
            self.window.status_message("View has no filename to track.")


def augment_color_scheme(view):
    # type: (sublime.View) -> None
    settings = GitSavvySettings()
    colors = settings.get('colors').get('log_graph')
    if not colors:
        return

    color_scheme = view.settings().get('color_scheme')
    if color_scheme.endswith(".tmTheme"):
        themeGenerator = XMLThemeGenerator(color_scheme)
    else:
        themeGenerator = JSONThemeGenerator(color_scheme)
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
        "GitSavvy Highlighted Matching Commit",
        MATCHING_COMMIT_SCOPE,
        background=colors['matching_commit_background'],
        foreground=colors['matching_commit_foreground'],
    )
    themeGenerator.apply_new_theme("log_graph_view", view)


DATE_FORMAT = 'human'
FALLBACK_DATE_FORMAT = 'format:%Y-%m-%d %H:%M'
DATE_FORMAT_STATE = 'trying'


class GsLogGraphRefreshCommand(TextCommand, GitCommand):

    """
    Refresh the current graph view with the latest commits.
    """

    def run(self, edit, navigate_after_draw=False):
        sublime.set_timeout_async(partial(self.run_async, navigate_after_draw))

    def run_async(self, navigate_after_draw=False):
        graph_content = prelude(self.view)
        graph_content += re.sub(
            r'(^[{}]*)\*'.format(GRAPH_CHAR_OPTIONS),
            r'\1' + COMMIT_NODE_CHAR,
            self.read_graph(),
            flags=re.MULTILINE
        )

        def program():
            # TODO: Preserve column if possible instead of going to the beginning
            #       of the commit hash blindly.
            # TODO: Only jump iff cursor is in viewport. If the user scrolled
            #       away (without changing the cursor) just set the cursor but
            #       do NOT show it.
            follow = self.view.settings().get('git_savvy.log_graph_view.follow')
            self.view.run_command("gs_replace_view_text", {"text": graph_content, "restore_cursors": True})
            if follow:
                navigate_to_symbol(self.view, follow)
            elif navigate_after_draw:  # on init
                self.view.run_command("gs_log_graph_navigate")

        enqueue_on_ui(program)

    def read_graph(self):
        # type: () -> str
        global DATE_FORMAT, DATE_FORMAT_STATE

        args = self.build_git_command()
        if DATE_FORMAT_STATE == 'trying':
            try:
                rv = self.git(
                    *args,
                    throw_on_stderr=True,
                    show_status_message_on_stderr=False,
                    show_panel_on_stderr=False
                )
            except GitSavvyError as e:
                if e.stderr and DATE_FORMAT in e.stderr:
                    DATE_FORMAT = FALLBACK_DATE_FORMAT
                    DATE_FORMAT_STATE = 'final'

                enqueue_on_worker(self.view.run_command, "gs_log_graph_refresh")
                return ''
            else:
                DATE_FORMAT_STATE = 'final'
                return rv

        else:
            return self.git(*args)

    def build_git_command(self):
        global DATE_FORMAT

        settings = self.view.settings()
        follow = self.savvy_settings.get("log_follow_rename")
        author = settings.get("git_savvy.log_graph_view.filter_by_author")
        all_branches = settings.get("git_savvy.log_graph_view.all_branches")
        args = [
            'log',
            '--graph',
            '--decorate',  # set explicitly for "decorate-refs-exclude" to work
            '--date={}'.format(DATE_FORMAT),
            '--pretty=format:%h%d %<|(80,trunc)%s | %ad, %an',
            '--follow' if self.file_path and follow else None,
            '--author={}'.format(author) if author else None,
            '--decorate-refs-exclude=refs/remotes/origin/HEAD',  # cosmetics
            '--exclude=refs/stash',
            '--all' if all_branches else None,
            '--simplify-by-decoration',
            '--sparse',
        ]

        branches = settings.get("git_savvy.log_graph_view.branches")
        if branches:
            args += branches

        if self.file_path:
            file_path = self.get_rel_path(self.file_path)
            args += ["--", file_path]

        return args


def prelude(view):
    # type: (sublime.View) -> str
    prelude = "\n"
    settings = view.settings()
    repo_path = settings.get("git_savvy.repo_path")
    file_path = settings.get("git_savvy.file_path")
    if file_path:
        rel_file_path = os.path.relpath(file_path, repo_path)
        prelude += "  FILE: {}\n".format(rel_file_path)
    elif repo_path:
        prelude += "  REPO: {}\n".format(repo_path)

    all_ = settings.get("git_savvy.log_graph_view.all_branches") or False
    branches = settings.get("git_savvy.log_graph_view.branches") or []
    prelude += (
        "  "
        + "  ".join(filter(None, [
            '[a]ll: true' if all_ else '[a]ll: false',
            " ".join(branches)
        ]))
        + "\n"
    )
    return prelude + "\n"


class GsLogGraphCommand(GsLogCommand):
    """
    Defines the main menu if you invoke `git: graph` or `git: graph current file`.

    Accepts `current_file: bool` or `file_path: str` as (keyword) arguments, and
    ensures that each of the defined actions/commands in `default_actions` are finally
    called with `file_path` set.
    """
    default_actions = [
        ["gs_log_graph_current_branch", "For current branch"],
        ["gs_log_graph_all_branches", "For all branches"],
        ["gs_log_graph_by_author", "Filtered by author"],
        ["gs_log_graph_by_branch", "Filtered by branch"],
    ]


class GsLogGraphCurrentBranch(WindowCommand, GitCommand):
    def run(self, file_path=None):
        self.window.run_command('gs_graph', {
            'file_path': file_path,
            'all': True,
            'follow': 'HEAD',
        })


class GsLogGraphAllBranches(WindowCommand, GitCommand):
    def run(self, file_path=None):
        self.window.run_command('gs_graph', {
            'file_path': file_path,
            'all': True,
        })


class GsLogGraphByAuthorCommand(WindowCommand, GitCommand):

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
            if index == -1:
                return
            selected_author = entries[index][3]
            self.window.run_command(
                'gs_graph',
                {'file_path': file_path, 'author': selected_author}
            )

        email = self.git("config", "user.email").strip()
        self.window.show_quick_panel(
            [entry[3] for entry in entries],
            on_select,
            flags=sublime.MONOSPACE_FONT,
            selected_index=[line[2] for line in entries].index(email)
        )


class GsLogGraphByBranchCommand(WindowCommand, GitCommand):
    _selected_branch = None

    def run(self, file_path=None):
        def on_select(branch):
            if branch:
                self._selected_branch = branch  # remember last selection
                self.window.run_command('gs_graph', {
                    'file_path': file_path,
                    'all': True,
                    'branches': [branch],
                    'follow': branch,
                })

        show_branch_panel(on_select, selected_branch=self._selected_branch)


class GsLogGraphNavigateCommand(GsNavigate):

    """
    Travel between commits. It is also used by compare_commit_view.
    """
    offset = 0

    def get_available_regions(self):
        return self.view.find_by_selector("constant.numeric.graph.commit-hash.git-savvy")


class GsLogGraphNavigateToHeadCommand(TextCommand):

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


class GsLogGraphToggleAllSetting(TextCommand, GitCommand):
    def run(self, edit):
        settings = self.view.settings()
        current = settings.get("git_savvy.log_graph_view.all_branches")
        next_state = not current
        settings.set("git_savvy.log_graph_view.all_branches", next_state)
        self.view.run_command("gs_log_graph_refresh")


class GsLogGraphCursorListener(EventListener, GitCommand):
    def is_applicable(self, view):
        # type: (sublime.View) -> bool
        return bool(view.settings().get("git_savvy.log_graph_view"))

    def on_activated(self, view):
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
        if not self.is_applicable(view) and show_commit_info.panel_is_visible(window):
            panel = PREVIOUS_OPEN_PANEL_PER_WINDOW.get(window.id(), None)
            if panel:
                window.run_command("show_panel", {"panel": panel})
            else:
                window.run_command('hide_panel')

        # Auto-show panel if the user switches back
        elif (
            self.is_applicable(view)
            and not show_commit_info.panel_is_visible(window)
            and self.savvy_settings.get("graph_show_more_commit_info")
        ):
            window.run_command("show_panel", {"panel": "output.show_commit_info"})

    # `on_selection_modified` triggers twice per mouse click
    # multiplied with the number of views into the same buffer,
    # hence it is *important* to throttle these events.
    # We do this seperately per side-effect. See the fn
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
            # remember the intent *if* the `active_view` is a 'log_graph'
            if self.is_applicable(view):
                self.savvy_settings.set("graph_show_more_commit_info", False)
            PREVIOUS_OPEN_PANEL_PER_WINDOW[window.id()] = None

        elif command_name == 'show_panel':
            view = window.active_view()
            if not view:
                return

            # Special case some panels. For these panels, showing them does not count
            # as intent to close the show_commit panel. It will thus reappear
            # automatically as soon as you focus the graph again. E.g. closing the
            # incremantal find panel via `<enter>` will bring the commit panel up
            # again.
            if args.get('panel') == "incremental_find":
                return

            toggle = args.get('toggle', False)
            panel = args.get('panel')
            if toggle and window.active_panel() == panel:  # <== actually *hide* panel
                # E.g. the same side-effect as in above "hide_panel" case
                if self.is_applicable(view):
                    self.savvy_settings.set("graph_show_more_commit_info", False)
                PREVIOUS_OPEN_PANEL_PER_WINDOW[window.id()] = None
            else:
                if panel == "output.show_commit_info":
                    self.savvy_settings.set("graph_show_more_commit_info", True)
                    PREVIOUS_OPEN_PANEL_PER_WINDOW[window.id()] = window.active_panel()
                    draw_info_panel(view)
                else:
                    if self.is_applicable(view):
                        self.savvy_settings.set("graph_show_more_commit_info", False)


PREVIOUS_OPEN_PANEL_PER_WINDOW = {}  # type: Dict[sublime.WindowId, Optional[str]]


def set_symbol_to_follow(view):
    # type: (sublime.View) -> None
    symbol = extract_symbol_to_follow(view)
    if symbol:
        view.settings().set('git_savvy.log_graph_view.follow', symbol)


def extract_symbol_to_follow(view):
    # type: (sublime.View) -> Optional[str]
    """Extract a symbol to follow."""
    try:
        # Intentional `b` (not `end()`!) because b is where the
        # cursor is. (If you select upwards b becomes < a.)
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return

    line_span = view.line(cursor)
    line_text = view.substr(line_span)
    return _extract_symbol_to_follow(view.id(), line_text)


@lru_cache(maxsize=512)
def _extract_symbol_to_follow(vid, _line_text):
    # type: (sublime.ViewId, str) -> Optional[str]
    view = sublime.View(vid)
    try:
        # Intentional `b` (not `end()`!) because b is where the
        # cursor is. (If you select upwards b becomes < a.)
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return None

    line_span = view.line(cursor)
    if view.match_selector(cursor, 'meta.graph.graph-line.head.git-savvy'):
        return 'HEAD'

    symbols_on_line = [
        symbol
        for r, symbol in view.symbols()
        if line_span.contains(r)
    ]
    if symbols_on_line:
        # git always puts the remotes first so we take
        # the last one which is (then) a local branch.
        return symbols_on_line[-1]

    line_text = view.substr(line_span)
    return extract_commit_hash(line_text)


def navigate_to_symbol(view, symbol):
    # type: (sublime.View, str) -> None
    region = _find_symbol(view, symbol)
    if region:
        set_and_show_cursor(view, region.begin())


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
            line_of_symbol = view.line(r)
            return extract_comit_hash_span(view, line_of_symbol)

    r = view.find(FIND_COMMIT_HASH + re.escape(symbol), 0)
    if not r.empty():
        line_of_symbol = view.line(r)
        return extract_comit_hash_span(view, line_of_symbol)
    return None


def extract_comit_hash_span(view, line_span):
    # type: (sublime.View, sublime.Region) -> Optional[sublime.Region]
    line_text = view.substr(line_span)
    match = COMMIT_LINE.search(line_text)
    if match:
        a, b = match.span('commit_hash')
        return sublime.Region(a + line_span.a, b + line_span.a)
    return None


FIND_COMMIT_HASH = "^[{graph_chars}]*[{node_chars}][{graph_chars}]* ".format(
    graph_chars=GRAPH_CHAR_OPTIONS, node_chars=COMMIT_NODE_CHAR_OPTIONS
)


@text_command
def set_and_show_cursor(view, cursor):
    # type: (sublime.View, int) -> None
    sel = view.sel()
    sel.clear()
    sel.add(cursor)
    view.show(sel, True)


def colorize_dots(view):
    # type: (sublime.View) -> None
    dots = tuple(find_dots(view))
    _colorize_dots(view.id(), dots)


def find_dots(view):
    # type: (sublime.View) -> Set[colorizer.Char]
    return set(_find_dots(view))


def _find_dots(view):
    # type: (sublime.View) -> Iterator[colorizer.Char]
    for s in view.sel():
        line_region = view.line(s.begin())
        line_content = view.substr(line_region)
        idx = line_content.find(COMMIT_NODE_CHAR)
        if idx > -1:
            yield colorizer.Char(view, line_region.begin() + idx)


@lru_cache(maxsize=1)
# ^- throttle side-effects
def _colorize_dots(vid, dots):
    # type: (sublime.ViewId, Tuple[colorizer.Char]) -> None
    view = sublime.View(vid)
    view.add_regions('gs_log_graph_dot', [d.region() for d in dots], scope=DOT_SCOPE)
    paths = [
        c.region()
        for path in map(colorizer.follow_path, dots)
        if len(path) > 1
        for c in path
    ]
    view.add_regions('gs_log_graph_follow_path', paths, scope=PATH_SCOPE)


def colorize_fixups(view):
    # type: (sublime.View) -> None
    dots = tuple(find_dots(view))
    _colorize_fixups(view.id(), dots)


@lru_cache(maxsize=1)
def _colorize_fixups(vid, dots):
    # type: (sublime.ViewId, Tuple[colorizer.Char]) -> None
    view = sublime.View(vid)
    message_regions = find_by_selector(view, 'meta.graph.message.git-savvy')
    extract_message = partial(
        message_from_fixup_squash_line, view.id(), message_regions=message_regions
    )
    matching_dots = list(filter_(
        find_matching_commit(view.id(), dot, message, message_regions)
        for dot, message in zip(dots, map(extract_message, dots))
        if message
    ))
    view.add_regions(
        'gs_log_graph_follow_fixups',
        [dot.region() for dot in matching_dots],
        scope=MATCHING_COMMIT_SCOPE
    )


def find_by_selector(view, selector):
    # type: (sublime.View, str) -> Tuple[Region, ...]
    # Same as `view.find_by_selector` but the result is hashable.
    return tuple(
        Region(r.a, r.b)
        for r in view.find_by_selector(selector)
    )


@lru_cache(maxsize=64)
def message_from_fixup_squash_line(vid, dot, message_regions):
    # type: (sublime.ViewId, colorizer.Char, Iterable[Region]) -> Optional[str]
    view = sublime.View(vid)
    message = commit_message_from_point(view, dot.pt, message_regions)
    if not message:
        return None
    # Truncated messages end with one or multiple "." dots which we
    # have to strip.
    if message.startswith('fixup! '):
        return message[7:].rstrip('.').strip()
    if message.startswith('squash! '):
        return message[8:].rstrip('.').strip()
    return None


def commit_message_from_point(view, pt, message_regions):
    # type: (sublime.View, int, Iterable[Region]) -> Optional[str]
    line_span = view.line(pt)
    for r in message_regions:
        if line_span.contains(r):
            return view.substr(r)
    else:
        return None


@lru_cache(maxsize=64)
def find_matching_commit(vid, dot, message, message_regions):
    # type: (sublime.ViewId, colorizer.Char, str, Iterable[Region]) -> Optional[colorizer.Char]
    view = sublime.View(vid)
    for dot in islice(follow_dots(dot), 0, 50):
        this_message = commit_message_from_point(view, dot.pt, message_regions)
        if this_message and this_message.startswith(message):
            return dot
    else:
        return None


def follow_dots(dot):
    # type: (colorizer.Char) -> Iterator[colorizer.Char]
    """Follow dot to dot omitting the path chars in between."""
    while True:
        try:
            dot = colorizer.follow_path(dot)[-1]
        except IndexError:
            break
        else:
            yield dot


def draw_info_panel(view):
    # type: (sublime.View) -> None
    """Extract line under the last cursor and draw info panel."""
    try:
        # Intentional `b` (not `end()`!) because b is where the
        # cursor is. (If you select upwards b becomes < a.)
        cursor = [s.b for s in view.sel()][-1]
    except IndexError:
        return

    line_span = view.line(cursor)
    line_text = view.substr(line_span)

    # Defer to a second fn to reduce side-effects
    draw_info_panel_for_line(view.id(), line_text)


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
        window.run_command("gs_show_commit_info", {"commit_hash": commit_hash})


def extract_commit_hash(line):
    match = COMMIT_LINE.search(line)
    return match.groupdict()['commit_hash'] if match else ""


class GsLogGraphToggleMoreInfoCommand(WindowCommand, GitCommand):

    """
    Toggle global `graph_show_more_commit_info` setting.
    """

    def run(self):
        if show_commit_info.panel_is_visible(self.window):
            self.window.run_command("hide_panel", {"panel": "output.show_commit_info"})
        else:
            self.window.run_command("show_panel", {"panel": "output.show_commit_info"})


if MYPY:
    from typing import TypedDict
    LineInfo = TypedDict('LineInfo', {
        'commit': str,
        'HEAD': str,
        'branches': List[str],
        'local_branches': List[str],
        'tags': List[str],
    }, total=False)


def describe_graph_line(line, remotes):
    # type: (str, Iterable[str]) -> Optional[LineInfo]
    match = COMMIT_LINE.match(line)
    if match is None:
        return None

    commit_hash = match.group("commit_hash")
    decoration = match.group("decoration")

    rv = {"commit": commit_hash}  # type: LineInfo
    if decoration:
        decoration = decoration[1:-1]  # strip parentheses
        names = decoration.split(", ")
        if names[0].startswith("HEAD"):
            head, *names = names
            if head == "HEAD":
                rv["HEAD"] = commit_hash
            else:
                branch = head[len("HEAD -> "):]
                rv["HEAD"] = branch
                names = [branch] + names
        branches, local_branches, tags = [], [], []
        for name in names:
            if name.startswith("tag: "):
                tags.append(name[len("tag: "):])
            else:
                branches.append(name)
                if not any(name.startswith(remote + "/") for remote in remotes):
                    local_branches.append(name)
        if branches:
            rv["branches"] = branches
        if local_branches:
            rv["local_branches"] = local_branches
        if tags:
            rv["tags"] = tags

    return rv


class GsLogGraphActionCommand(WindowCommand, GitCommand):
    selected_index = 0

    def run(self):
        view = self.window.active_view()
        if not view:
            return

        sel = view.sel()
        if len(sel) > 1:
            self.window.status_message("You can only do actions on one commit at a time.")
            return

        cursor = sel[0].b
        line_span = view.line(cursor)
        line_text = view.substr(line_span)
        remotes = set(self.get_remotes().keys())
        info = describe_graph_line(line_text, remotes)
        if not info:
            return

        commit_hash = info["commit"]
        file_path = self.file_path
        actions = []  # type: List[Tuple[str, Callable[[], None]]]
        actions += [
            (
                "Show commit", partial(self.show_commit, commit_hash)
            )
        ]
        actions += [
            ("Checkout '{}'".format(branch_name), partial(self.checkout, branch_name))
            for branch_name in info.get("local_branches", [])
            if info.get("HEAD") != branch_name
        ]

        good_commit_name = (
            info["tags"][0]
            if info.get("tags")
            else commit_hash
        )
        if "HEAD" not in info or info["HEAD"] != commit_hash:
            actions += [
                (
                    "Checkout '{}' detached".format(good_commit_name),
                    partial(self.checkout, good_commit_name)
                ),
            ]

        if file_path:
            actions += [
                ("Show file at commit", partial(self.show_file_at_commit, commit_hash, file_path)),
                ("Blame file at commit", partial(self.blame_file_atcommit, commit_hash, file_path)),
                (
                    "Checkout file at commit",
                    partial(self.checkout_file_at_commit, commit_hash, file_path)
                )
            ]

        actions += [
            ("Create tag", partial(self.create_tag, commit_hash))
        ]
        actions += [
            ("Delete '{}'".format(tag_name), partial(self.delete_tag, tag_name))
            for tag_name in info.get("tags", [])
        ]

        if "HEAD" not in info:
            actions += [
                ("Cherry-pick commit", partial(self.cherry_pick, commit_hash)),
            ]

        actions += [
            ("Revert commit", partial(self.revert_commit, commit_hash)),
            (
                "Compare {}against ...".format("file " if file_path else ""),
                partial(self.compare_against, commit_hash, file_path)
            ),
            (
                "Diff {}against workdir".format("file " if file_path else ""),
                partial(self.diff_commit, commit_hash, file_path)
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

    def checkout(self, commit_hash):
        self.git("checkout", commit_hash)
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def show_commit(self, commit_hash):
        self.window.run_command("gs_show_commit", {"commit_hash": commit_hash})

    def create_tag(self, commit_hash):
        self.window.run_command("gs_tag_create", {"target_commit": commit_hash})

    def delete_tag(self, tag_name):
        self.git("tag", "-d", tag_name)
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def cherry_pick(self, commit_hash):
        self.git("cherry-pick", commit_hash)
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def revert_commit(self, commit_hash):
        self.git("revert", commit_hash)
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def compare_against(self, commit_hash, file_path):
        self.window.run_command("gs_compare_against", {
            "base_commit": commit_hash,
            "file_path": file_path
        })

    def copy_sha(self, commit_hash):
        sublime.set_clipboard(self.git("rev-parse", commit_hash).strip())

    def diff_commit(self, commit_hash, file_path):
        self.window.run_command("gs_diff", {
            "in_cached_mode": False,
            "file_path": file_path,
            "base_commit": commit_hash,
            "disable_stage": True
        })

    def show_file_at_commit(self, commit_hash, file_path):
        self.window.run_command(
            "gs_show_file_at_commit",
            {"commit_hash": commit_hash, "filepath": file_path})

    def blame_file_atcommit(self, commit_hash, file_path):
        self.window.run_command(
            "gs_blame",
            {"commit_hash": commit_hash, "file_path": file_path})

    def checkout_file_at_commit(self, commit_hash, file_path):
        self.checkout_ref(commit_hash, fpath=file_path)
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)
