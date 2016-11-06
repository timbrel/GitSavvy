import itertools
import sublime
from ...common import util


class PanelActionMixin(object):
    """
    Use this mixin to display quick panel, select from pre-defined actions and
    execute the matching class method.
    """
    selected_index = 0  # Every instantiated class get their own `selected_index`
    default_actions = None  # must be set by inheriting class

    def run(self, *args, **kwargs):
        self.update_actions()
        self.show_panel()

    def update_actions(self):
        self.actions = self.default_actions[:]  # copy default actions

    def show_panel(self, actions=None):
        self.window.show_quick_panel(
            [a[1] for a in actions or self.actions],
            self.on_action_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=self.selected_index,
        )

    def on_action_selection(self, index):
        if index == -1:
            return

        self.selected_index = index  # set last selected choice as default
        action = self.actions[index][0]
        getattr(self, action)()


def show_paginated_panel(items, on_done, flags=None, selected_index=None, on_highlight=None,
                         limit=6000, next_message=None):
    """
    Display items in quick panel with pagination, and execute on_done
    when item is selected.

    items: can be either a list or a generator.
    on_done: a callback will take one argument
    limit: the number of items per page
    next_message: a message of next page, default is ">>> NEXT PAGE >>>"

    If the elements are tuples of the form `(value1, value2)`,
    `value1` would be displayed via quick panel and `value2` will be passed to
    `on_done` when the item is selected. Furthermore, if the quick panel is
    cancelled, `None` will be passed to `on_done`.

    """

    pp = PaginatedPanel(
            items,
            on_done,
            flags=flags,
            selected_index=selected_index,
            on_highlight=on_highlight,
            limit=limit,
            next_message=next_message)
    pp.show()


def show_log_panel(entries, on_done, limit=6000, selected_index=None):
    """
    Display log entries in quick panel with pagination, and execute on_done(commit)
    when item is selected. `entries` can be either a list or a generator of LogEnty.

    """
    lp = LogPanel(entries, on_done, limit=limit, selected_index=selected_index)
    lp.show()


class PaginatedPanel:

    """
    A version of QuickPanel which supports pagination.
    """
    flags = None
    next_message = ">>> NEXT PAGE >>>"
    limit = 6000
    selected_index = None
    on_highlight = None

    def __init__(self, items, on_done, **kwargs):
        self.skip = 0
        self.item_generator = (item for item in items)
        self.on_done = on_done
        for option in ['flags', 'selected_index', 'on_highlight',
                       'limit', 'next_message', ]:
            if option in kwargs:
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
        self.load_next_batch()

        if len(self.display_list) == self.limit:
            self.display_list.append(self.next_message)

        kwargs = {}
        if self.flags:
            kwargs["flags"] = self.flags
        if self.selected_index and self.skip <= self.selected_index < self.skip + self.limit:
            kwargs["selected_index"] = self.selected_index - self.skip
        if self.on_highlight:
            kwargs["on_highlight"] = self.on_highlight

        sublime.active_window().show_quick_panel(
            self.display_list,
            self.on_selection,
            **kwargs
        )

    def on_selection(self, index):
        if index == self.limit:
            self.skip = self.skip + self.limit
            sublime.set_timeout(self.show, 10)
        elif self.ret_list:
            if index == -1:
                self.on_done(None)
            else:
                self.on_done(self.ret_list[index])
        else:
            self.on_done(self.skip + index)


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

    def on_highlight(self, index):
        sublime.set_timeout_async(lambda: self.on_highlight_async(index))

    def on_highlight_async(self, index):
        if index == self.limit:
            return
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        show_more = savvy_settings.get("log_show_more_commit_info")
        if not show_more:
            return
        sublime.active_window().run_command(
            "gs_show_commit_info", {"commit_hash": self.ret_list[index]})

    def on_selection(self, index):
        sublime.set_timeout_async(lambda: self.on_selection_async(index), 10)

    def on_selection_async(self, index):
        sublime.active_window().run_command("hide_panel", {"panel": "output.show_commit_info"})
        if index == -1:
            return
        if index == self.limit:
            self.selected_index = 0
            sublime.set_timeout_async(self.show, 10)
            return
        self.on_done(self.ret_list[index])
