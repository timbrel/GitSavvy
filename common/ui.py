from collections import OrderedDict
from textwrap import dedent
import re

import sublime
from sublime_plugin import TextCommand

from . import util


interfaces = {}
edit_views = {}
subclasses = []

EDIT_DEFAULT_HELP_TEXT = "## To finalize your edit, press {super_key}+Enter.  To cancel, close the view.\n"


class Interface():

    interface_type = ""
    read_only = True
    syntax_file = ""
    word_wrap = False

    template = ""

    _initialized = False

    def __new__(cls, repo_path=None, **kwargs):
        """
        Search for intended interface in active window - if found, bring it
        to focus and return it instead of creating a new interface.
        """
        window = sublime.active_window()
        for view in window.views():
            vset = view.settings()
            if vset.get("git_savvy.interface") == cls.interface_type and \
               vset.get("git_savvy.repo_path") == repo_path:
                window.focus_view(view)
                return interfaces[view.id()]

        return super().__new__(cls)

    def __init__(self, repo_path=None, view=None):
        if self._initialized:
            return
        self._initialized = True

        self.regions = {}

        subclass_attrs = (getattr(self, attr) for attr in vars(self.__class__).keys())

        self.partials = {
            attr.key: attr
            for attr in subclass_attrs
            if callable(attr) and hasattr(attr, "key")
            }

        for attr in vars(self.__class__).keys():
            if attr.startswith("template"):
                setattr(self, attr, dedent(getattr(self, attr)))

        if view:
            self.view = view
            self.render(nuke_cursors=False)
        else:
            self.create_view(repo_path)
            sublime.set_timeout_async(self.on_new_dashboard, 0)

        if hasattr(self, "tab_size"):
            self.view.settings().set("tab_size", self.tab_size)

        interfaces[self.view.id()] = self

    def create_view(self, repo_path):
        window = sublime.active_window()
        self.view = window.new_file()

        self.view.settings().set("git_savvy.repo_path", repo_path)
        self.view.set_name(self.title())
        self.view.settings().set("git_savvy.{}_view".format(self.interface_type), True)
        self.view.settings().set("git_savvy.tabbable", True)
        self.view.settings().set("git_savvy.interface", self.interface_type)
        self.view.settings().set("word_wrap", self.word_wrap)
        self.view.set_syntax_file(self.syntax_file)
        self.view.set_scratch(True)
        self.view.set_read_only(self.read_only)
        util.view.disable_other_plugins(self.view)

        self.render()
        window.focus_view(self.view)

        return self.view

    def render(self, nuke_cursors=False):
        self.clear_regions()
        if hasattr(self, "pre_render"):
            self.pre_render()
        rendered = self._render_template()
        self.view.run_command("gs_new_content_and_regions", {
            "content": rendered,
            "regions": self.regions,
            "nuke_cursors": nuke_cursors
            })

    def _render_template(self):
        """
        Generate new content for the view given the interface template
        and partial content.  As partial content is added to the rendered
        template, add regions to `self.regions` with the key, start, and
        end of each partial.
        """
        rendered = self.template

        keyed_content = self.get_keyed_content()
        for key, new_content in keyed_content.items():
            new_content_len = len(new_content)
            pattern = re.compile(r"\{(<+ )?" + key + r"\}")

            match = pattern.search(rendered)
            while match:
                start, end = match.span()
                backspace_group = match.groups()[0]
                backspaces = backspace_group.count("<") if backspace_group else 0
                start -= backspaces

                rendered = rendered[:start] + new_content + rendered[end:]

                self.adjust(start, end - start, new_content_len)
                if new_content_len:
                    self.regions[key] = [start, start+new_content_len]

                match = pattern.search(rendered)

        return rendered

    def adjust(self, idx, orig_len, new_len):
        """
        When interpolating template variables, update region ranges for previously-evaluated
        variables, that are situated later on in the output/template string.
        """
        shift = new_len - orig_len
        for key, region in self.regions.items():
            if region[0] > idx:
                region[0] += shift
                region[1] += shift
            elif region[1] > idx or region[0] == idx:
                region[1] += shift

    def get_keyed_content(self):
        keyed_content = OrderedDict(
            (key, render_fn())
            for key, render_fn in self.partials.items()
            )

        for key in keyed_content:
            output = keyed_content[key]
            if isinstance(output, tuple):
                sub_template, complex_partials = output
                keyed_content[key] = sub_template

                for render_fn in complex_partials:
                    keyed_content[render_fn.key] = render_fn()

        return keyed_content

    def update(self, key, content):
        self.view.run_command("gs_update_region", {
            "key": "git_savvy_interface." + key,
            "content": content
            })

    def clear_regions(self):
        for key in self.regions.keys():
            self.view.erase_regions("git_savvy_interface." + key)
        self.regions = {}

    def get_view_regions(self, key):
        return self.view.get_regions("git_savvy_interface." + key)

    def get_selection_line(self):
        selections = self.view.sel()
        if not selections or len(selections) > 1:
            sublime.status_message("Please make a selection.")
            return None

        selection = selections[0]
        return selection, util.view.get_lines_from_regions(self.view, [selection])[0]

    def get_selection_lines_in_region(self, region):
        return util.view.get_lines_from_regions(
            self.view,
            self.view.sel(),
            valid_ranges=self.get_view_regions(region)
            )

    def on_new_dashboard(self):
        pass


def partial(key):
    def decorator(fn):
        fn.key = key
        return fn
    return decorator


class GsNewContentAndRegionsCommand(TextCommand):

    def run(self, edit, content, regions, nuke_cursors=False):
        selections = self.view.sel()

        if selections and not nuke_cursors:
            cursors_row_col = [self.view.rowcol(cursor.a) for cursor in selections]
        else:
            cursors_row_col = [(0, 0)]

        selections.clear()

        is_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), content)
        self.view.set_read_only(is_read_only)

        for row, col in cursors_row_col:
            pt = self.view.text_point(row, col)
            selections.add(sublime.Region(pt, pt))

        for key, region_range in regions.items():
            a, b = region_range
            self.view.add_regions("git_savvy_interface." + key, [sublime.Region(a, b)])

        if self.view.settings().get("git_savvy.interface"):
            self.view.run_command("gs_handle_vintageous")


class GsUpdateRegionCommand(TextCommand):

    def run(self, edit, key, content):
        is_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        for region in self.view.get_regions(key):
            self.view.replace(edit, region, content)
        self.view.set_read_only(is_read_only)


def register_listeners(InterfaceClass):
    subclasses.append(InterfaceClass)


def get_interface(view_id):
    return interfaces.get(view_id, None)


class GsInterfaceCloseCommand(TextCommand):

    """
    Clean up references to interfaces for closed views.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        view_id = self.view.id()
        if view_id in interfaces:
            del interfaces[view_id]


class GsInterfaceRefreshCommand(TextCommand):

    """
    Re-render GitSavvy interface view.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        interface_type = self.view.settings().get("git_savvy.interface")
        for InterfaceSubclass in subclasses:
            if InterfaceSubclass.interface_type == interface_type:
                existing_interface = interfaces.get(self.view.id(), None)
                if existing_interface:
                    existing_interface.render(nuke_cursors=False)
                else:
                    interface = InterfaceSubclass(view=self.view)
                    interfaces[interface.view.id()] = interface


class EditView():

    def __init__(self, content, on_done, repo_path, help_text=None, window=None):
        self.window = window or sublime.active_window()
        self.view = self.window.new_file()

        self.view.set_scratch(True)
        self.view.set_read_only(False)
        self.view.set_name("EDIT")
        self.view.set_syntax_file("Packages/GitSavvy/syntax/make_commit.sublime-syntax")
        self.view.settings().set("word_wrap", False)
        self.view.settings().set("git_savvy.edit_view", True)
        self.view.settings().set("git_savvy.repo_path", repo_path)

        self.on_done = on_done
        self.render(content, help_text)

        edit_views[self.view.id()] = self

    def render(self, starting_content, help_text):
        regions = {}

        starting_content += "\n\n"

        regions["content"] = (0, len(starting_content))
        content = starting_content + (help_text or EDIT_DEFAULT_HELP_TEXT).format(super_key=util.super_key)
        regions["help"] = (len(starting_content), len(content))

        self.view.run_command("gs_new_content_and_regions", {
            "content": content,
            "regions": regions,
            "nuke_cursors": True
            })


class GsEditViewCompleteCommand(TextCommand):

    """
    Invoke callback with edit view content.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        edit_view = edit_views.get(self.view.id(), None)
        if not edit_view:
            sublime.error_message("Unable to complete edit.  Please try again.")
            return

        help_region = self.view.get_regions("git_savvy_interface.help")[0]
        content_before = self.view.substr(sublime.Region(0, help_region.begin()))
        content_after = self.view.substr(sublime.Region(help_region.end(), self.view.size() - 1))
        content = (content_before + content_after).strip()

        self.view.window().focus_view(self.view)
        self.view.window().run_command("close_file")

        edit_view.on_done(content)


class GsEditViewCloseCommand(TextCommand):

    """
    Clean up references to closed edit views.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        view_id = self.view.id()
        if view_id in edit_views:
            del edit_views[view_id]
