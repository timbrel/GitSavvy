import sublime
from . import util


def show_paginated_panel(items, on_done, limit=6000, **kargs):
    """
    Display items in quick panel with pagination, and execute on_done(index)
    when item is selected. `items` can be either a list or a generator.
    """
    pp = PaginatedPanel(items, on_done, limit, **kargs)
    pp.show()


def show_log_panel(entries, on_done, limit=6000, selected_index=None):
    """
    Display log entries in quick panel with pagination, and execute on_done(commit)
    when item is selected. `entries` can be either a list or a generator of LogEnty.

    """
    lp = LogPanel(entries, on_done, limit, selected_index)
    lp.show()


class PaginatedPanel:

    """
    A version of QuickPanel which supports pagination.
    """

    def __init__(self, items, on_done, limit=6000, flags=None,
                 selected_index=None, on_highlight=None):
        self.skip = 0
        self.limit = limit
        self.items = (entry for entry in items)
        self.on_done = on_done
        self.flags = flags
        self.selected_index = selected_index
        self.on_highlight = on_highlight

    def next_batch(self):
        idx = 0
        batch = []
        try:
            while idx < self.limit:
                batch.append(next(self.items))
                idx = idx + 1
        except StopIteration:
            pass
        return batch

    def format_batch(self, batch):
        return batch

    @property
    def next_message(self):
        return ">>> NEXT {} items >>>".format(self.limit)

    def show(self):

        batch = self.next_batch()
        batch = self.format_batch(batch)

        if len(batch) == self.limit:
            batch.append(self.next_message)

        args = {}
        if self.flags:
            args.update({"flags": self.flags})
        if self.selected_index:
            args.update({"selected_index": self.selected_index})
        if self.on_highlight:
            args.update({"on_highlight": self.on_highlight})

        sublime.active_window().show_quick_panel(
            batch,
            self.on_selection,
            **args
        )

    def on_selection(self, index):
        if index == self.limit:
            self.skip = self.skip + self.limit
            sublime.set_timeout(self.show, 10)
        else:
            self.on_done(self.skip + index)


class LogPanel(PaginatedPanel):

    def __init__(self, items, on_done, limit=6000, selected_index=None):
        self.limit = limit
        self.items = (entry for entry in items)
        self.on_done = on_done
        self.limit = limit
        self.flags = sublime.MONOSPACE_FONT | sublime.KEEP_OPEN_ON_FOCUS_LOST
        self.selected_index = selected_index
        self.on_highlight = self.on_entry_highlight

    def format_batch(self, batch):
        self._hashes = [entry.long_hash for entry in batch]
        return [[
                    entry.short_hash + " " + entry.summary,
                    entry.author + ", " + util.dates.fuzzy(entry.datetime)
                ] for entry in batch]

    @property
    def next_message(self):
        return [">>> NEXT {} COMMITS >>>".format(self.limit),
                "Skip this set of commits and choose from the next-oldest batch."]

    def on_entry_highlight(self, index):
        sublime.set_timeout_async(lambda: self.on_entry_highlight_async(index))

    def on_entry_highlight_async(self, index):
        if index == self.limit:
            return
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        show_more = savvy_settings.get("log_show_more_commit_info")
        if not show_more:
            return
        sublime.active_window().run_command(
            "gs_show_commit_info", {"commit_hash": self._hashes[index]})

    def on_selection(self, index):
        sublime.set_timeout_async(lambda: self.on_selection_async(index), 10)

    def on_selection_async(self, index):
        sublime.active_window().run_command("hide_panel", {"panel": "output.show_commit_info"})
        if index == -1:
            return
        if index == self.limit:
            sublime.set_timeout_async(self.show, 10)
            return
        self.on_done(self._hashes[index])
