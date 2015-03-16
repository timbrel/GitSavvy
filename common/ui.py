from collections import OrderedDict

import sublime
from sublime_plugin import TextCommand

from . import util


class Interface():

    read_only = True
    view_type = ""
    syntax_file = ""
    word_wrap = False

    dedent = 0
    skip_first_line = False

    template = ""

    def __init__(self, view_attrs=None):
        self.view_attrs = view_attrs or {}
        subclass_attrs = (getattr(self, attr) for attr in vars(self.__class__).keys())

        self.partials = {
            attr.key: attr
            for attr in subclass_attrs
            if callable(attr) and hasattr(attr, "key")
            }

        if self.skip_first_line:
            self.template = self.template[self.template.find("\n") + 1:]
        if self.dedent:
            for attr in vars(self.__class__).keys():
                if attr.startswith("template"):
                    setattr(self, attr, "\n".join(
                        line[self.dedent:] if len(line) >= self.dedent else line
                        for line in getattr(self, attr).split("\n")
                        ))

    def create_view(self):
        window = sublime.active_window()
        self.view = window.new_file()

        for k, v in self.view_attrs.items():
            self.view.settings().set(k, v)

        self.view.set_name(self.title())
        self.view.settings().set("git_savvy.{}_view".format(self.view_type), True)
        self.view.settings().set("git_savvy.interface", True)
        self.view.settings().set("word_wrap", self.word_wrap)
        self.view.set_syntax_file(self.syntax_file)
        self.view.set_scratch(True)
        self.view.set_read_only(self.read_only)
        util.view.disable_other_plugins(self.view)

        self.render()
        window.focus_view(self.view)

        return self.view

    def render(self):
        if hasattr(self, "pre_render"):
            self.pre_render()

        rendered = self.template

        regions = []
        keyed_content = self.get_keyed_content()
        for key, new_content in keyed_content.items():
            interpol = "{" + key + "}"
            interpol_len = len(interpol)
            cursor = 0
            match = rendered.find(interpol)
            while match >= 0:
                regions.append((key, (match, match+len(new_content))))
                rendered = rendered[:match] + new_content + rendered[match+interpol_len:]

                match = rendered.find(interpol, cursor)

        self.view.run_command("gs_new_content_and_regions", {
            "content": rendered,
            "regions": regions,
            "nuke_cursors": True
            })

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


def partial(key):
    def decorator(fn):
        fn.key = key
        return fn
    return decorator


class GsNewContentAndRegionsCommand(TextCommand):

    def run(self, edit, content, regions, nuke_cursors=False):
        cursors_num = len(self.view.sel())
        is_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), content)
        self.view.set_read_only(is_read_only)

        if not cursors_num or nuke_cursors:
            selections = self.view.sel()
            selections.clear()
            pt = sublime.Region(0, 0)
            selections.add(pt)

        for key, region_range in regions:
            a, b = region_range
            self.view.add_regions("git_savvy_interface." + key, [sublime.Region(a, b)])


class GsUpdateRegionCommand(TextCommand):

    def run(self, edit, key, content):
        is_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        for region in self.view.get_regions(key):
            self.view.replace(edit, region, content)
        self.view.set_read_only(is_read_only)
