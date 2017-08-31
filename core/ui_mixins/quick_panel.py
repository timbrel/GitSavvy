import itertools
import sublime
from ...common import util
from ..git_command import GitCommand


class PanelActionMixin(object):
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
    default_actions = None  # must be set by inheriting class
    async_action = False    # if True, executes action with set_timeout_async

    def run(self, *args, **kwargs):
        self.update_actions()
        self.show_panel(pre_selected_index=kwargs.get('pre_selected_index', None))

    def update_actions(self):
        self.actions = self.default_actions[:]  # copy default actions

    def show_panel(self, actions=None, pre_selected_index=None):
        window = self.window if hasattr(self, 'window') else self.view.window()
        if pre_selected_index:
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
        if hasattr(self, 'window'):
            return self.window.run_command
        elif hasattr(self, 'view'):
            return self.view.run_command
        else:
            return sublime.run_command

    def get_arguments(self, selected_action):
        """Prepares `run_command` arguments:
          - (required) Command name is 1st argument
          - (optional) args is 2nd (and next) arguments
          - (optional) kwargs are simply keyword arguments
        """
        args, kwargs = super().get_arguments(selected_action)
        return ((selected_action[0], ) + args), kwargs


def show_remote_panel(on_done, show_option_all=False, selected_remote=None):
    """
    Show a quick panel with remotes. The callback `on_done(remote)` will
    be called when a remote is selected. If the panel is cancelled, `None`
    will be passed to `on_done`.

    on_done: a callable
    show_option_all: whether the option "All remotes" should be shown. `True` will
                be passed to `on_done` if the all remotes option is selected.
    """
    rp = RemotePanel(on_done, show_option_all, selected_remote)
    rp.show()
    return rp


class RemotePanel(GitCommand):

    def __init__(self, on_done, show_option_all=False, selected_remote=None):
        self.window = sublime.active_window()
        self.on_done = on_done
        self.selected_remote = selected_remote
        self.show_option_all = show_option_all

    def show(self):
        self.remotes = list(self.get_remotes().keys())

        if not self.remotes:
            self.window.show_quick_panel(["There are no remotes available."], None)
            return

        # should we proceed directly if len(self.remotes) == 1 !?
        # GsRemoteRemoveCommand may not work well if we proceed directly

        if self.show_option_all and len(self.remotes) > 1:
            self.remotes.insert(0, "All remotes.")

        if self.last_remote_used in self.remotes:
            pre_selected_index = self.remotes.index(self.last_remote_used)
        else:
            pre_selected_index = 0

        self.window.show_quick_panel(
            self.remotes,
            self.on_remote_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=pre_selected_index
        )

    def on_remote_selection(self, index):
        if index == -1:
            self.on_done(None)
        elif self.show_option_all and len(self.remotes) > 1 and index == 0:
            self.on_done(True)
        else:
            self.remote = self.remotes[index]
            self.last_remote_used = self.remote
            self.on_done(self.remote)


def show_branch_panel(
        on_done,
        local_branches_only=False,
        remote_branches_only=False,
        ignore_current_branch=False,
        ask_remote_first=False,
        local_branch=None,
        selected_branch=None):
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
        local_branches_only,
        remote_branches_only,
        ignore_current_branch,
        ask_remote_first,
        selected_branch)
    bp.show()
    return bp


class BranchPanel(GitCommand):

    def __init__(
            self, on_done, local_branches_only=False, remote_branches_only=False,
            ignore_current_branch=False, ask_remote_first=False, selected_branch=None):
        self.window = sublime.active_window()
        self.on_done = on_done
        self.local_branches_only = local_branches_only
        self.remote_branches_only = True if ask_remote_first else remote_branches_only
        self.ignore_current_branch = ignore_current_branch
        self.ask_remote_first = ask_remote_first
        self.selected_branch = selected_branch

    def show(self):
        if self.ask_remote_first:
            show_remote_panel(
                lambda remote: sublime.set_timeout_async(
                    lambda: self.on_remote_selection(remote), 100))
        else:
            self.select_branch(remote=None)

    def on_remote_selection(self, remote):
        if not remote:
            return

        self.select_branch(remote)

    def select_branch(self, remote=None):

        if self.local_branches_only:
            self.all_branches = [b.name_with_remote for b in self.get_branches() if not b.remote]
        elif self.remote_branches_only:
            self.all_branches = [b.name_with_remote for b in self.get_branches() if b.remote]
        else:
            self.all_branches = [b.name_with_remote for b in self.get_branches()]

        if self.ignore_current_branch:
            current_branch = self.get_current_branch_name()
            self.all_branches = [b for b in self.all_branches if b != current_branch]

        if remote:
            self.all_branches = [b for b in self.all_branches if b.startswith(remote + "/")]

        if not self.all_branches:
            self.window.show_quick_panel(["There are no branches available."], None)
            return

        self.window.show_quick_panel(
            self.all_branches,
            self.on_branch_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=self.get_pre_selected_branch_index(remote)
        )

    def get_pre_selected_branch_index(self, remote):
        pre_selected_index = None
        if self.selected_branch is None:
            self.selected_branch = self.get_current_branch_name()

        if self.ask_remote_first:
            pre_selected_remote_branch = "{}/{}".format(remote, self.selected_branch)
            if pre_selected_remote_branch in self.all_branches:
                pre_selected_index = self.all_branches.index(pre_selected_remote_branch)

        if pre_selected_index is None:
            if self.selected_branch is not None and self.selected_branch in self.all_branches:
                pre_selected_index = self.all_branches.index(self.selected_branch)

        if pre_selected_index is None:
            pre_selected_index = 0

        return pre_selected_index

    def on_branch_selection(self, index):
        if index == -1:
            self.branch = None
        else:
            self.branch = self.all_branches[index]

        self.on_done(self.branch)


def show_paginated_panel(items, on_done, flags=None, selected_index=None, on_highlight=None,
                         limit=6000, format_item=None, next_message=None, status_message=None):
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

    next_message: a message of next page, default is ">>> NEXT PAGE >>>"

    status_message: a message to display at statusbar while loading the entries.

    If the elements are tuples of the form `(value1, value2)`,
    `value1` would be displayed via quick panel and `value2` will be passed to
    `on_done`, `selected_index` and `on_highlight`.
    Furthermore, if the quick panel is cancelled, `None` will be passed to `on_done`.
    """

    pp = PaginatedPanel(
            items,
            on_done,
            flags=flags,
            selected_index=selected_index,
            on_highlight=on_highlight,
            limit=limit,
            format_item=format_item,
            next_message=next_message,
            status_message=status_message)
    pp.show()
    return pp


class PaginatedPanel:

    """
    A version of QuickPanel which supports pagination.
    """
    flags = sublime.MONOSPACE_FONT | sublime.KEEP_OPEN_ON_FOCUS_LOST
    next_message = ">>> NEXT PAGE >>>"
    status_message = None
    limit = 6000
    selected_index = None
    on_highlight = None

    def __init__(self, items, on_done, **kwargs):
        self._is_empty = True
        self._is_done = False
        self.skip = 0
        self.item_generator = (item for item in items)
        self.on_done = on_done
        for option in ['flags', 'selected_index', 'on_highlight',
                       'limit', 'format_item', 'next_message', 'status_message']:
            # need to check the nullness of the options to avoid overriding the default
            # methods, e.g. `format_item` and `on_hightight` of LogPanel
            if option in kwargs and kwargs[option] is not None:
                setattr(self, option, kwargs[option])

    def load_next_batch(self):
        self.display_list = []
        self.ret_list = []
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
            sublime.status_message(self.status_message)
        try:
            self.load_next_batch()
        finally:
            if self.status_message:
                sublime.status_message("")

        if self.display_list and self._is_empty:
            self._is_empty = False

        if len(self.display_list) == self.limit:
            self.display_list.append(self.next_message)
        else:
            # done
            self._is_done = True

        kwargs = {}
        if self.flags:
            kwargs["flags"] = self.flags

        selected_index = self.get_selected_index()

        if selected_index:
            kwargs["selected_index"] = selected_index

        if self.on_highlight:
            kwargs["on_highlight"] = self._on_highlight

        if self.display_list:
            sublime.active_window().show_quick_panel(
                self.display_list,
                self._on_selection,
                **kwargs
            )

    def get_selected_index(self):
        if callable(self.selected_index):
            for idx, entry in enumerate(self.ret_list):
                if self.selected_index(entry):
                    return idx
        elif self.selected_index and self.skip <= self.selected_index < self.skip + self.limit:
            return self.selected_index - self.skip

    def _on_highlight(self, index):
        if index == self.limit or index == -1:
            return
        elif self.ret_list:
            self.on_highlight(self.ret_list[index])
        else:
            self.on_highlight(self.skip + index)

    def _on_selection(self, index):
        if index == self.limit:
            self.skip = self.skip + self.limit
            sublime.set_timeout_async(self.show, 10)
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


def show_log_panel(entries, on_done, limit=6000, selected_index=None, on_highlight=None):
    """
    Display log entries in quick panel with pagination, and execute on_done(commit)
    when item is selected. `entries` can be either a list or a generator of LogEnty.

    """
    lp = LogPanel(
        entries,
        on_done,
        limit=limit,
        selected_index=selected_index,
        on_highlight=on_highlight)
    lp.show()
    return lp


class LogPanel(PaginatedPanel):

    flags = sublime.MONOSPACE_FONT | sublime.KEEP_OPEN_ON_FOCUS_LOST

    def format_item(self, entry):
        return ([entry.short_hash + " " + entry.summary,
                 entry.author + ", " + util.dates.fuzzy(entry.datetime)],
                entry.long_hash)

    @property
    def next_message(self):
        return [">>> NEXT {} COMMITS >>>".format(self.limit),
                "Skip this set of commits and choose from the next-oldest batch."]

    def on_highlight(self, commit):
        sublime.set_timeout_async(lambda: self.on_highlight_async(commit))

    def on_highlight_async(self, commit):
        if not commit:
            return
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        show_more = savvy_settings.get("log_show_more_commit_info")
        if not show_more:
            return
        sublime.active_window().run_command(
            "gs_show_commit_info", {"commit_hash": commit})

    def on_selection(self, commit):
        self.commit = commit
        sublime.set_timeout_async(lambda: self.on_selection_async(commit), 10)

    def on_selection_async(self, commit):
        sublime.active_window().run_command("hide_panel", {"panel": "output.show_commit_info"})
        self.on_done(commit)


def show_stash_panel(on_done, **kwargs):
    """
    Display stash entries in quick panel with pagination, and execute on_done(stash)
    when item is selected. `entries` can be either a list or a generator of LogEnty.
    """

    sp = StashPanel(on_done, **kwargs)
    sp.show()
    return sp


class StashPanel(PaginatedPanel, GitCommand):

    def __init__(self, on_done, **kwargs):
        self.window = sublime.active_window()
        super().__init__(self.get_stashes(), on_done, **kwargs)

    def format_item(self, entry):
        return (entry.id + " " + entry.description, entry.id)
