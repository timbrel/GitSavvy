from __future__ import annotations
import itertools
import sublime
from ...common import util
from ..git_command import GitCommand
from GitSavvy.core.fns import filter_, maybe
from ..ui__quick_panel import show_panel, show_noop_panel


from typing import Callable, Dict, List, Literal, Optional, Union
from ..git_mixins.history import LogEntry


NO_REMOTES_MESSAGE = "You have not configured any remotes."


class PanelActionMixin(GitCommand):
    """
    Use this mixin to initially display a quick panel, select from pre-defined
    actions and execute the matching instance method.

    The `default_actions` are copied into self.actions and should be a list of
    list/tuple items of at least length of 2, e.g:

        default_actions = [
            ['some_method', 'Run some method'],
            ['other_method', 'Run some other method'],
            ['some_method', 'Run method with arg1 and arg2', ('arg1', 'arg2')],
            ['some_method', 'Run method with kwargs1: foo', (), {'kwarg1': 'foo'}],
        ]

    Will result in the following method calls accordingly:

        self.some_method()
        self.other_method()
        self.other_method('arg1', 'arg2')
        self.other_method(kwarg1='foo')
    """
    selected_index = 0      # Every instance gets it's own `selected_index`
    default_actions = None  # type: List  # must be set by inheriting class
    async_action = False    # if True, executes action with set_timeout_async

    def run(self, *args, **kwargs):
        self.update_actions()
        self.show_panel(pre_selected_index=kwargs.get('pre_selected_index', None))

    def update_actions(self):
        self.actions = self.default_actions[:]  # copy default actions

    def show_panel(self, actions=None, pre_selected_index=None):
        window = self._current_window()
        assert window
        if pre_selected_index is not None:
            self.on_action_selection(pre_selected_index)
            return

        window.show_quick_panel(
            [a[1] for a in actions or self.actions],
            self.on_action_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=self.selected_index,
        )

    def on_action_selection(self, index):
        if index == -1:
            return

        self.selected_index = index  # set last selected choice as default
        selected_action = self.actions[index]
        func = self.get_callable(selected_action)
        args, kwargs = self.get_arguments(selected_action)
        if self.async_action:
            sublime.set_timeout_async(lambda: func(*args, **kwargs))
        else:
            func(*args, **kwargs)

    def get_callable(self, selected_action):
        return getattr(self, selected_action[0])

    def get_arguments(self, selected_action):
        if len(selected_action) == 3:
            return selected_action[2], {}
        elif len(selected_action) == 4:
            return selected_action[2:4]
        return (), {}


class PanelCommandMixin(PanelActionMixin):
    """
    Same basic functionality as PanelActionMixin, except that it executes a given
    sublime command rather than a given instance method. For example:

        default_actions = [
            ['foo', 'Run FooCommand'],
            ['bar', 'Run BarCommand with arg1 and arg2', ('arg1', 'arg2')],
            ['bar', 'Run BarCommand with kwargs1: foo', ({'kwarg1': 'foo'}, )],
            ['bar', 'Run BarCommand with kwargs1: foo', (), {'kwarg1': 'foo'}],
        ]

    Will result in the following commands accordingly:

        self.window.run_command("foo")
        self.window.run_command("bar", 'arg1', 'arg2')
        self.window.run_command("bar", {'kwarg1': 'foo'})
        self.window.run_command("bar", kwarg1='foo')

    """

    def get_callable(self, selected_action):
        return (
            maybe(lambda: self.view.run_command)  # type: ignore[attr-defined]
            or maybe(lambda: self.window.run_command)  # type: ignore[attr-defined]
            or sublime.run_command
        )

    def get_arguments(self, selected_action):
        """Prepares `run_command` arguments:
          - (required) Command name is 1st argument
          - (optional) args is 2nd (and next) arguments
          - (optional) kwargs are simply keyword arguments
        """
        args, kwargs = super().get_arguments(selected_action)
        return ((selected_action[0], ) + args), kwargs


def show_remote_panel(
    on_done,  # type: Callable[[str], None]
    *,
    on_cancel=lambda: None,  # type: Callable[[], None]
    show_option_all=False,  # type: bool
    allow_direct=False,  # type: bool
    show_url=False,  # type: bool
    remotes=None,  # type: Dict[str, str]
):
    # type: (...) -> RemotePanel
    """
    Show a quick panel with remotes. The callback `on_done(remote)` will
    be called when a remote is selected. If the panel is cancelled, `None`
    will be passed to `on_done`.

    on_done: a callable
    show_option_all: whether the option "All remotes" should be shown. `<ALL>` will
                be passed to `on_done` if the all remotes option is selected.
    """
    rp = RemotePanel(
        on_done,
        on_cancel,
        show_option_all,
        allow_direct,
        show_url,
        remotes,
    )
    rp.show()
    return rp


class RemotePanel(GitCommand):

    def __init__(
        self,
        on_done,  # type: Callable[[str], None]
        on_cancel=lambda: None,  # type: Callable[[], None]
        show_option_all=False,  # type: bool
        allow_direct=False,  # type: bool
        show_url=False,  # type: bool
        remotes=None,  # type: Dict[str, str]
    ):
        # type: (...) -> None
        if show_option_all and show_url:
            raise TypeError(
                "'show_option_all' and 'show_url' are mutual exclusive. "
            )
        self.window = sublime.active_window()
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.show_option_all = show_option_all
        self.allow_direct = allow_direct
        self.show_url = show_url
        self.storage_key: Literal["last_remote_used", "last_remote_used_with_option_all"] = (
            "last_remote_used_with_option_all"
            if self.show_option_all
            else "last_remote_used"
        )
        self._remotes = remotes

    def show(self):
        # type: () -> None
        _remotes = self.get_remotes() if self._remotes is None else self._remotes
        self.remotes = list(_remotes.keys())

        if not self.remotes:
            show_noop_panel(self.window, NO_REMOTES_MESSAGE)
            return

        if self.allow_direct and len(self.remotes) == 1:
            self.on_remote_selection(0)
            return

        if self.show_option_all and len(self.remotes) > 1:
            self.remotes.insert(0, "All remotes.")

        last_remote_used = self.current_state().get(self.storage_key, "origin")
        if last_remote_used in self.remotes:
            pre_selected_index = self.remotes.index(last_remote_used)
        else:
            pre_selected_index = 0

        self.window.show_quick_panel(
            (
                [[remote, _remotes[remote]] for remote in self.remotes]
                if self.show_url else
                self.remotes
            ),
            self.on_remote_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=pre_selected_index
        )

    def on_remote_selection(self, index):
        # type: (int) -> None
        if index == -1:
            self.on_cancel()
        elif self.show_option_all and len(self.remotes) > 1 and index == 0:
            self.update_store({self.storage_key: "<ALL>"})  # type: ignore[misc]
            self.on_done("<ALL>")
        else:
            remote = self.remotes[index]
            self.update_store({self.storage_key: remote})  # type: ignore[misc]
            self.on_done(remote)


def show_branch_panel(
        on_done: Callable[[str], None],
        *,
        on_cancel: Callable[[], None] = lambda: None,
        local_branches_only: bool = False,
        remote_branches_only: bool = False,
        ignore_current_branch: bool = False,
        ask_remote_first: bool = False,
        selected_branch: Optional[str] = None,
        merged: Optional[bool] = None,
):
    """
    Show a quick panel with branches. The callback `on_done(branch)` will
    be called when a branch is selected. If the panel is cancelled, `None`
    will be passed to `on_done`.

    on_done: a callable
    ask_remote_first: whether remote should be asked before the branch panel
            if `False`. the options will be in forms of `remote/branch`
    selected_branch: if `ask_remote_first`, the selected branch will be
            `{remote}/{selected_branch}`
    """
    bp = BranchPanel(
        on_done,
        on_cancel,
        local_branches_only,
        remote_branches_only,
        ignore_current_branch,
        ask_remote_first,
        selected_branch,
        merged,
    )
    bp.show()
    return bp


class BranchPanel(GitCommand):

    def __init__(
            self,
            on_done: Callable[[str], None],
            on_cancel: Callable[[], None] = lambda: None,
            local_branches_only: bool = False,
            remote_branches_only: bool = False,
            ignore_current_branch: bool = False,
            ask_remote_first: bool = False,
            selected_branch: Optional[str] = None,
            merged: Optional[bool] = None,
    ):
        self.window = sublime.active_window()
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.local_branches_only = local_branches_only
        self.remote_branches_only = True if ask_remote_first else remote_branches_only
        self.ignore_current_branch = ignore_current_branch
        self.ask_remote_first = ask_remote_first
        self.selected_branch = selected_branch
        self.merged = merged

    def show(self):
        if self.ask_remote_first:
            show_remote_panel(self.select_branch, allow_direct=True)
        else:
            self.select_branch(remote=None)

    def select_branch(self, remote=None):
        branches = self.get_branches(merged=self.merged)
        if self.local_branches_only:
            self.all_branches = [b.canonical_name for b in branches if b.is_local]
        elif self.remote_branches_only:
            self.all_branches = [b.canonical_name for b in branches if b.is_remote]
        else:
            self.all_branches = [b.canonical_name for b in branches]

        current_branch = next((b.name for b in branches if b.active), None)
        if self.ignore_current_branch:
            self.all_branches = [b for b in self.all_branches if b != current_branch]
        elif self.selected_branch is None and not self.remote_branches_only:
            self.selected_branch = current_branch

        if remote:
            self.all_branches = [b for b in self.all_branches if b.startswith(remote + "/")]

        if not self.all_branches:
            show_noop_panel(self.window, "There are no branches available.")
            return

        if self.selected_branch:
            selected_index = self.get_pre_selected_branch_index(self.selected_branch, remote)
            if selected_index:
                self.all_branches = (
                    [self.all_branches[selected_index]]
                    + self.all_branches[:selected_index]
                    + self.all_branches[selected_index + 1:]
                )
                selected_index = 0
        else:
            selected_index = 0

        self.window.show_quick_panel(
            self.all_branches,
            self.on_branch_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=selected_index
        )

    def get_pre_selected_branch_index(self, selected_branch, remote):
        if remote:
            branch_candidates = ["{}/{}".format(remote, selected_branch), selected_branch]
        else:
            branch_candidates = [selected_branch]

        for candidate in branch_candidates:
            try:
                return self.all_branches.index(candidate)
            except ValueError:
                pass
        else:
            return 0

    def on_branch_selection(self, index):
        if index == -1:
            self.on_cancel()
        else:
            self.on_done(self.all_branches[index])


def show_paginated_panel(items, on_done, **kwargs):

    """
    A version of QuickPanel which supports pagination.
    """
    _kwargs = {}
    for option in ['flags', 'selected_index', 'on_highlight', 'limit', 'format_item',
                   'next_page_message', 'empty_page_message', 'last_page_empty_message',
                   'status_message']:
        if option in kwargs:
            _kwargs[option] = kwargs[option]

    pp = PaginatedPanel(items, on_done, **_kwargs)
    pp.show()
    return pp


class PaginatedPanel:

    """
    Display items in quick panel with pagination, and execute on_done
    when item is selected.

    items: can be either a list or a generator.

    on_done: a callback will take one argument

    limit: the number of items per page

    selected_index: an integer or a callable returning boolean.
                    If callable, takes either an integer or an entry.

    on_highlight: a callable, takes either an integer or an entry.

    format_item: a function to format each item

    next_page_message: a message of next page, default is ">>> NEXT PAGE >>>"

    empty_page_message: a message to show when the first page is empty.

    last_page_empty_message: a message to show when the last page is empty. It is
                             less confusing to inform user than to show nothing.

    status_message: a message to display at statusbar while loading the entries.

    If the elements are tuples of the form `(value1, value2)`,
    `value1` would be displayed via quick panel and `value2` will be passed to
    `on_done`, `selected_index` and `on_highlight`.
    Furthermore, if the quick panel is cancelled, `None` will be passed to `on_done`.
    """

    flags = sublime.MONOSPACE_FONT | sublime.KEEP_OPEN_ON_FOCUS_LOST
    next_page_message: str | list[str] = ">>> NEXT PAGE >>>"
    empty_page_message = None  # type: Optional[str]
    last_page_empty_message = ">>> LAST PAGE >>>"
    status_message = None  # type: Optional[str]
    limit = 6000
    selected_index = None  # type: Union[Optional[int], Callable[[object], bool]]
    on_highlight = None  # type: Optional[Callable[[Union[int|object]], None]]

    def __init__(self, items, on_done, **kwargs):
        self._is_empty = True
        self._is_done = False
        self._empty_message_shown = False
        self.skip = 0
        self.item_generator = (item for item in items)
        self.on_done = on_done
        for option in kwargs:
            setattr(self, option, kwargs[option])

    def load_next_batch(self):
        self.display_list: list[str | list[str]] = []
        self.ret_list = []  # type: List[object]
        for item in itertools.islice(self.item_generator, self.limit):
            self.extract_item(item)
        if self.ret_list and len(self.ret_list) != len(self.display_list):
            raise Exception("the lengths of display_list and ret_list are different.")

    def extract_item(self, item):
        item = self.format_item(item)
        if type(item) is tuple and len(item) == 2:
            self.display_list.append(item[0])
            self.ret_list.append(item[1])
        else:
            self.display_list.append(item)

    def format_item(self, item):
        return item

    def show(self):
        if self.status_message:
            sublime.active_window().status_message(self.status_message)
        try:
            self.load_next_batch()
        finally:
            if self.status_message:
                sublime.active_window().status_message("")

        if len(self.display_list) == self.limit:
            self.display_list.append(self.next_page_message)
            self._is_empty = False

        elif len(self.display_list) == 0:
            if self._is_empty:
                # first page but empty
                if self.empty_page_message:
                    self.display_list.append(self.empty_page_message)
            else:
                # last page but empty
                if self.last_page_empty_message:
                    self.display_list.append(self.last_page_empty_message)
            self._is_done = True
            self._empty_message_shown = True
        else:
            self._is_empty = False
            self._is_done = True

        if self.display_list:
            sublime.active_window().show_quick_panel(
                self.display_list,
                self._on_selection,
                flags=self.flags,
                selected_index=self.get_selected_index(),
                on_highlight=self._on_highlight
            )

    def get_selected_index(self) -> int:
        if callable(self.selected_index):
            for idx, entry in enumerate(self.ret_list):
                if self.selected_index(entry):
                    return idx
        elif self.selected_index and self.skip <= self.selected_index < self.skip + self.limit:
            return self.selected_index - self.skip
        return 0

    def _on_highlight(self, index: int):
        if not self.on_highlight:
            return
        if self._empty_message_shown:
            return

        if index == self.limit or index == -1:
            return
        elif self.ret_list:
            self.on_highlight(self.ret_list[index])
        else:
            self.on_highlight(self.skip + index)

    def _on_selection(self, index):
        if self._empty_message_shown:
            return

        if index == self.limit:
            self.skip = self.skip + self.limit
            sublime.set_timeout_async(self.show)
        elif self.ret_list:
            if index == -1:
                self.on_selection(None)
            else:
                self.on_selection(self.ret_list[index])
        else:
            if index == -1:
                self.on_selection(-1)
            else:
                self.on_selection(self.skip + index)

    def on_selection(self, value):
        self.value = value
        self.on_done(value)

    def is_empty(self):
        return self._is_empty

    def is_done(self):
        return self._is_done


def show_log_panel(entries, on_done, **kwargs):
    """
    Display log entries in quick panel with pagination, and execute on_done(commit)
    when item is selected. `entries` can be either a list or a generator of LogEntry.

    """
    _kwargs = {}
    for option in ['selected_index', 'limit', 'on_highlight']:
        if option in kwargs:
            _kwargs[option] = kwargs[option]

    lp = LogPanel(entries, on_done, **_kwargs)
    lp.show()
    return lp


def short_ref(ref):
    def simplify(r):
        if r.startswith('HEAD -> '):
            return r[8:]

        if r.startswith('tag: '):
            return r[5:]

        return r

    def remote_diverged_from_local(refs, r):
        try:
            a, b = r.split('/', 1)
        except ValueError:
            return True
        else:
            return False if b in refs else True

    if not ref:
        return ''

    refs = ref.split(', ')
    refs = [simplify(r) for r in refs]
    refs = [r for r in refs if remote_diverged_from_local(refs, r)]
    refs = ["|{}|".format(r) for r in refs]

    return ' '.join(refs)


class LogPanel(PaginatedPanel):
    def __init__(self, *args, **kwargs):
        self.next_page_message = [
            ">>> NEXT {} COMMITS >>>".format(self.limit),
            "Skip this set of commits and choose from the next-oldest batch."
        ]
        super().__init__(*args, **kwargs)

    def format_item(self, entry):
        return (
            [
                "  ".join(filter_((entry.short_hash, short_ref(entry.ref), entry.summary))),
                ", ".join(filter_((entry.author, util.dates.fuzzy(entry.datetime)))),
            ],
            entry.long_hash
        )

    def on_selection(self, commit):
        self.on_done(commit)


class LogHelperMixin(GitCommand):
    def show_log_panel(self, action, preselected_commit=lambda items: -1):
        # type: (Callable[[LogEntry], None], Callable[[List[LogEntry]], int]) -> None
        window = self._current_window()
        if not window:
            return

        items = self.log(limit=100)

        def on_done(idx: int) -> None:
            window.run_command("hide_panel", {"panel": "output.show_commit_info"})
            entry = items[idx]
            action(entry)

        def on_cancel() -> None:
            window.run_command("hide_panel", {"panel": "output.show_commit_info"})

        def on_highlight(idx: int) -> None:
            entry = items[idx]
            window.run_command("gs_show_commit_info", {
                "commit_hash": entry.short_hash
            })

        def format_item(entry: LogEntry) -> str:
            return "  ".join(filter_((
                entry.short_hash,
                short_ref(entry.ref) if not entry.ref.startswith("HEAD ->") else "",
                entry.summary
            )))

        preselected_idx = preselected_commit(items)
        show_panel(
            window,
            map(format_item, items),
            on_done,
            on_cancel,
            on_highlight,
            selected_index=preselected_idx,
            flags=sublime.MONOSPACE_FONT | sublime.KEEP_OPEN_ON_FOCUS_LOST,
        )


def show_stash_panel(on_done, **kwargs):
    """
    Display stash entries in quick panel with pagination, and execute on_done(stash)
    when item is selected.
    """

    sp = StashPanel(on_done, **kwargs)
    sp.show()
    return sp


class StashPanel(PaginatedPanel, GitCommand):
    empty_page_message = "There are no stashes available."

    def __init__(self, on_done, **kwargs):
        self.window = sublime.active_window()
        super().__init__(self.get_stashes(), on_done, **kwargs)

    def format_item(self, entry):
        return (entry.id + " " + entry.description, entry.id)
