from textwrap import dedent
import re

import sublime
from sublime_plugin import TextCommand

from . import util
from ..core.runtime import enqueue_on_worker, on_worker
from ..core.settings import GitSavvySettings
from ..core.utils import focus_view
from GitSavvy.core.base_commands import GsTextCommand
from GitSavvy.core.fns import flatten
from GitSavvy.core.view import replace_view_content


__all__ = (
    "gs_new_content_and_regions",
    "gs_update_region",
    "gs_interface_close",
    "gs_interface_refresh",
    "gs_interface_toggle_help",
    "gs_interface_toggle_popup_help",
    "gs_edit_view_complete",
    "gs_edit_view_close",
)


MYPY = False
if MYPY:
    from typing import Dict, Iterable, Iterator, List, Optional, Protocol, Set, Tuple, Type, Union
    SectionRegions = Dict[str, sublime.Region]

    class SectionFn(Protocol):
        key = ''  # type: str

        def __call__(self) -> 'Union[str, Tuple[str, List[SectionFn]]]':
            pass


interfaces = {}  # type: Dict[sublime.ViewId, Interface]
edit_views = {}
subclasses = []  # type: List[Type[Interface]]

EDIT_DEFAULT_HELP_TEXT = "## To finalize your edit, press {super_key}+Enter.  To cancel, close the view.\n"


class _PrepareInterface(type):
    def __init__(cls, cls_name, bases, attrs):
        for attr_name, value in attrs.items():
            if attr_name.startswith("template"):
                setattr(cls, attr_name, dedent(value))

        cls.sections = [
            attr_name
            for attr_name, attr in attrs.items()
            if callable(attr) and hasattr(attr, "key")
        ]


def show_interface(window, repo_path, typ):
    # type: (sublime.Window, str, str) -> None
    for view in window.views():
        vset = view.settings()
        if (
            vset.get("git_savvy.interface") == typ
            and vset.get("git_savvy.repo_path") == repo_path
        ):
            focus_view(view)
            ensure_interface_object(view, typ)
            break
    else:
        create_interface(window, repo_path, typ)


def create_interface(window, repo_path, typ):
    # type: (sublime.Window, str, str) -> Interface
    return klass_for_typ(typ).create_view(window, repo_path)


def ensure_interface_object(view, typ):
    # type: (sublime.View, str) -> Interface
    vid = view.id()
    try:
        return interfaces[vid]
    except KeyError:
        interface = interfaces[vid] = klass_for_typ(typ)(view=view)
        return interface


def klass_for_typ(typ):
    # type: (str) -> Type[Interface]
    for klass in subclasses:
        if klass.interface_type == typ:
            return klass
    raise RuntimeError(
        "Assertion failed! "
        "no class found for interface type '{}'".format(typ)
    )


class Interface(metaclass=_PrepareInterface):
    interface_type = ""
    syntax_file = ""
    template = ""
    sections = []    # type: List[str]

    def __init__(self, view):
        # type: (sublime.View) -> None
        self.view = view
        interfaces[self.view.id()] = self
        self.on_create()

    @classmethod
    def create_view(cls, window, repo_path):
        # type: (sublime.Window, str) -> Interface
        window = sublime.active_window()
        view = window.new_file()

        view.settings().set("git_savvy.repo_path", repo_path)
        view.settings().set("git_savvy.{}_view".format(cls.interface_type), True)
        view.settings().set("git_savvy.tabbable", True)
        view.settings().set("git_savvy.interface", cls.interface_type)
        view.settings().set("git_savvy.help_hidden", GitSavvySettings().get("hide_help_menu"))
        view.set_syntax_file(cls.syntax_file)
        view.set_scratch(True)
        view.set_read_only(True)
        util.view.disable_other_plugins(view)

        interface = cls(view=view)
        interface.after_view_creation(view)  # before first render
        interface.render()
        enqueue_on_worker(interface.on_new_dashboard)  # after first render

        focus_view(view)
        return interface

    def title(self):
        # type: () -> str
        raise NotImplementedError

    def after_view_creation(self, view):
        """
        Hook template method called after the view has been created.
        Can be used to further manipulate the view and store state on it.
        """
        pass

    def on_new_dashboard(self):
        """
        Hook template method called after the first render.
        """
        pass

    def on_create(self):
        """
        Hook template method called after a new interface object has been created.
        """
        pass

    def on_close(self):
        """
        Hook template method called after a view has been closed.
        """
        pass

    def pre_render(self):
        pass

    def reset_cursor(self):
        pass

    def render(self, nuke_cursors=False):
        self.pre_render()
        content, regions = self._render_template()
        self.draw(self.title(), content, regions)
        if nuke_cursors:
            self.reset_cursor()

    def draw(self, title, content, regions):
        # type: (str, str, SectionRegions) -> None
        self.view.set_name(title)
        self.view.run_command("gs_new_content_and_regions", {
            "content": content,
            "regions": {key: region_as_tuple(region) for key, region in regions.items()}
        })

    def _render_template(self):
        # type: () -> Tuple[str, SectionRegions]
        """
        Generate new content for the view given the interface template
        and partial content.  As partial content is added to the rendered
        template, compute and build up `regions` with the key, start, and
        end of each partial.
        """
        rendered = self.template
        regions = {}  # type: SectionRegions

        for key, new_content in self._get_keyed_content():
            new_content_len = len(new_content)
            pattern = re.compile(r"\{(<+ )?" + key + r"\}")

            match = pattern.search(rendered)
            while match:
                start, end = match.span()
                backspace_group = match.groups()[0]
                backspaces = backspace_group.count("<") if backspace_group else 0
                start -= backspaces

                rendered = rendered[:start] + new_content + rendered[end:]

                self._adjust_region_positions(regions, start, end - start, new_content_len)
                if new_content_len:
                    regions[key] = sublime.Region(start, start + new_content_len)

                match = pattern.search(rendered)

        return rendered, regions

    def _adjust_region_positions(self, regions, idx, orig_len, new_len):
        # type: (SectionRegions, int, int, int) -> None
        """
        When interpolating template variables, update region ranges for previously-evaluated
        variables, that are situated later on in the output/template string.
        """
        shift = new_len - orig_len
        for key, region in regions.items():
            if region.a > idx:
                region.a += shift
                region.b += shift
            elif region.b > idx or region.a == idx:
                region.b += shift

    def _get_keyed_content(self):
        # type: () -> Iterator[Tuple[str, str]]
        render_fns = [getattr(self, name) for name in self.sections]  # type: List[SectionFn]
        for fn in render_fns:
            result = fn()
            if isinstance(result, tuple):
                result, partials = result
                render_fns += partials
            yield fn.key, result

    def update_view_section(self, key, content):
        self.view.run_command("gs_update_region", {
            "key": "git_savvy_interface." + key,
            "content": content
        })


def section(key):
    def decorator(fn):
        fn.key = key
        return fn
    return decorator


def indent_by_2(text):
    return "\n".join(line[2:] for line in text.split("\n"))


class gs_new_content_and_regions(TextCommand):
    current_region_names = set()  # type: Set[str]

    def run(self, edit, content, regions):
        replace_view_content(self.view, content)

        for key, region_range in regions.items():
            self.view.add_regions("git_savvy_interface." + key, [region_from_tuple(region_range)])

        for key in self.current_region_names - regions.keys():
            self.view.erase_regions("git_savvy_interface." + key)

        self.current_region_names = regions.keys()

        if self.view.settings().get("git_savvy.interface"):
            self.view.run_command("gs_handle_vintageous")
            self.view.run_command("gs_handle_arrow_keys")


class gs_update_region(TextCommand):

    def run(self, edit, key, content):
        is_read_only = self.view.is_read_only()
        self.view.set_read_only(False)
        for region in self.view.get_regions(key):
            self.view.replace(edit, region, content)
        self.view.set_read_only(is_read_only)


def register_listeners(InterfaceClass):
    subclasses.append(InterfaceClass)


def get_interface(view_id):
    # type: (sublime.ViewId) -> Optional[Interface]
    return interfaces.get(view_id, None)


class InterfaceCommand(GsTextCommand):
    interface_type = None  # type: Type[Interface]
    interface = None  # type: Interface

    def run_(self, edit_token, args):
        vid = self.view.id()
        interface = get_interface(vid)
        if not interface:
            raise RuntimeError(
                "Assertion failed! "
                "no dashboard registered for {}".format(vid))
        if not isinstance(interface, self.interface_type):
            raise RuntimeError(
                "Assertion failed! "
                "registered interface `{}` is not of type `{}`"
                .format(interface, self.interface_type.__name__)
            )
        self.interface = interface
        return super().run_(edit_token, args)

    def region_name_for(self, section):
        # type: (str) -> str
        return "git_savvy_interface." + section


def region_as_tuple(region):
    # type: (sublime.Region) -> Tuple[int, int]
    return region.begin(), region.end()


def region_from_tuple(tuple_):
    # type: (Tuple[int, int]) -> sublime.Region
    return sublime.Region(*tuple_)


def unique_regions(regions):
    # type: (Iterable[sublime.Region]) -> Iterator[sublime.Region]
    # Regions are not hashable so we unpack them to tuples,
    # then use set, finally pack them again
    return map(region_from_tuple, set(map(region_as_tuple, regions)))


def unique_selected_lines(view):
    # type: (sublime.View) -> List[sublime.Region]
    return list(unique_regions(flatten(view.lines(s) for s in view.sel())))


def extract_by_selector(view, item_selector, within_section=None):
    # type: (sublime.View, str, str) -> List[str]
    selected_lines = unique_selected_lines(view)
    items = view.find_by_selector(item_selector)
    acceptable_sections = (
        view.get_regions(within_section)
        if within_section else
        [sublime.Region(0, view.size())]
    )
    return [
        view.substr(item)
        for section in acceptable_sections
        for line in selected_lines if section.contains(line)
        for item in items if line.contains(item)
    ]


class gs_interface_close(TextCommand):

    """
    Clean up references to interfaces for closed views.
    """

    def run(self, edit):
        view_id = self.view.id()
        interface = get_interface(view_id)
        if interface:
            interface.on_close()
            enqueue_on_worker(lambda: interfaces.pop(view_id))


class gs_interface_refresh(TextCommand):

    """
    Re-render GitSavvy interface view.
    """

    @on_worker
    def run(self, edit, nuke_cursors=False):
        # type: (object, bool) -> None
        vid = self.view.id()
        interface = interfaces.get(vid, None)
        if interface:
            interface.render(nuke_cursors=nuke_cursors)
            return

        interface_type = self.view.settings().get("git_savvy.interface")
        for cls in subclasses:
            if cls.interface_type == interface_type:
                interface = interfaces[vid] = cls(view=self.view)
                interface.render(nuke_cursors=nuke_cursors)
                break


class gs_interface_toggle_help(TextCommand):

    """
    Toggle GitSavvy help.
    """

    def run(self, edit):
        current_help = bool(self.view.settings().get("git_savvy.help_hidden"))
        self.view.settings().set("git_savvy.help_hidden", not current_help)
        self.view.run_command("gs_interface_refresh")


class gs_interface_toggle_popup_help(TextCommand):

    """
    Toggle GitSavvy popup help.
    """

    def run(self, edit, view_name, popup_max_width=800, popup_max_height=900):
        css = sublime.load_resource("Packages/GitSavvy/popups/style.css")
        html = (
            sublime.load_resource("Packages/GitSavvy/popups/" + view_name + ".html")
            .format(css=css, super_key=util.super_key)
        )
        visible_region = self.view.visible_region()
        self.view.show_popup(html, 0, visible_region.begin(), popup_max_width, popup_max_height)


class EditView():

    def __init__(self, content, on_done, repo_path, help_text=None, window=None):
        self.window = window or sublime.active_window()
        self.view = self.window.new_file()

        self.view.set_scratch(True)
        self.view.set_read_only(False)
        self.view.set_name("EDIT")
        self.view.set_syntax_file("Packages/GitSavvy/syntax/make_commit.sublime-syntax")
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
            "regions": regions
        })


class gs_edit_view_complete(TextCommand):

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

        self.view.close()
        edit_view.on_done(content)


class gs_edit_view_close(TextCommand):

    """
    Clean up references to closed edit views.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        view_id = self.view.id()
        if view_id in edit_views:
            del edit_views[view_id]
