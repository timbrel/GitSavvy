from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
import inspect
import re
from textwrap import dedent
import threading
from weakref import WeakKeyDictionary

import sublime
from sublime_plugin import TextCommand

from . import util
from .theme_generator import ThemeGenerator
from ..core.commands import multi_selector
from ..core.runtime import enqueue_on_worker, run_on_new_thread
from ..core.settings import GitSavvySettings
from ..core.utils import flash, focus_view
from GitSavvy.core import store
from GitSavvy.core.base_commands import GsTextCommand
from GitSavvy.core.fns import flatten
from GitSavvy.core.git_command import GitCommand
from GitSavvy.core.view import replace_view_content


__all__ = (
    "gs_new_content_and_regions",
    "gs_update_region",
    "gs_interface_close",
    "gs_interface_refresh",
    "gs_interface_toggle_help",
    "gs_interface_show_commit",
    "gs_edit_view_complete",
    "gs_edit_view_close",
)


from typing import (
    AbstractSet, Callable, Dict, Generic, Iterable, Iterator, List, MutableMapping,
    Protocol, Set, Tuple, Type, TypeVar, Union, cast
)
T = TypeVar("T")
T_fn = TypeVar("T_fn", bound=Callable)
T_state = TypeVar("T_state", Dict, MutableMapping)

SectionRegions = Dict[str, sublime.Region]
RenderFnReturnType = Union[str, Tuple[str, List["SectionFn"]]]
T_R = TypeVar("T_R", bound=RenderFnReturnType, covariant=True)
FunctionReturning = Callable[..., T]


class RenderFn(Protocol[T_R]):
    def __call__(self, *args, **kwargs) -> T_R:
        pass


class SectionFn(RenderFn[T_R]):
    key = ''  # type: str


interfaces = {}  # type: Dict[sublime.ViewId, Interface]
edit_views = {}
known_interface_types = {}  # type: Dict[str, Type[Interface]]

EDIT_DEFAULT_HELP_TEXT = "## To finalize your edit, press {super_key}+Enter.  To cancel, close the view.\n"


class _PrepareInterface(type):
    def __init__(cls: "Type[T]", cls_name, bases, attrs):
        for attr_name, value in attrs.items():
            if attr_name.startswith("template"):
                setattr(cls, attr_name, dedent(value))

        cls.sections = [
            attr_name
            for attr_name, attr in attrs.items()
            if callable(attr) and hasattr(attr, "key")
        ]

        if cls_name == "Interface":
            # Bail out as the class `Interface` is not yet
            # defined.  (This `__init__` here is called *while*
            # defining `Interface`.)
            return

        if issubclass(cls, Interface):
            known_interface_types[cls.interface_type] = cls


def show_interface(window, repo_path, typ):
    # type: (sublime.Window, str, str) -> None
    for view in window.views():
        vset = view.settings()
        if (
            vset.get("git_savvy.interface") == typ
            and vset.get("git_savvy.repo_path") == repo_path
        ):
            focus_view(view)
            ensure_interface_object(view)
            break
    else:
        create_interface(window, repo_path, typ)


def create_interface(window, repo_path, typ):
    # type: (sublime.Window, str, str) -> Interface
    return klass_for_typ(typ).create_view(window, repo_path)


def ensure_interface_object(view):
    # type: (sublime.View) -> Interface
    vid = view.id()
    try:
        return interfaces[vid]
    except KeyError:
        typ = view.settings().get("git_savvy.interface")
        if not typ:
            raise RuntimeError(
                "Assertion failed! "
                "The view {} has no interface information set".format(view)
            )
        interface = interfaces[vid] = klass_for_typ(typ)(view=view)
        return interface


def klass_for_typ(typ):
    # type: (str) -> Type[Interface]
    try:
        return known_interface_types[typ]
    except KeyError:
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
        run_on_new_thread(augment_color_scheme, view)

        interface = cls(view=view)
        interface.after_view_creation(view)  # before first render
        interface.render()
        interface.on_new_dashboard()         # after first render

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

    def render(self):
        self.pre_render()
        self.just_render()

    def just_render(self):
        # type: () -> None
        content, regions = self._render_template()
        with self.keep_cursor_on_something():
            self.draw(self.title(), content, regions)

    @contextmanager
    def keep_cursor_on_something(self):
        # type: () -> Iterator[None]
        yield

    def cursor_is_on_something(self, what):
        # type: (str) -> bool
        view = self.view
        return any(
            view.match_selector(s.begin(), what)
            for s in view.sel()
        )

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


def augment_color_scheme(view):
    # type: (sublime.View) -> None
    settings = GitSavvySettings()
    colors = settings.get('colors').get('dashboard')
    if not colors:
        return

    themeGenerator = ThemeGenerator.for_view(view)
    themeGenerator.add_scoped_style(
        "GitSavvy Multiselect Marker",
        multi_selector.MULTISELECT_SCOPE,
        background=colors['multiselect_foreground'],
        foreground=colors['multiselect_background'],
    )
    themeGenerator.apply_new_theme("dashboard_view", view)


def distinct_until_state_changed(just_render_fn):
    """Custom `lru_cache`-look-alike to minimize redraws."""
    previous_states = WeakKeyDictionary()  # type: WeakKeyDictionary

    @wraps(just_render_fn)
    def wrapper(self, *args, **kwargs):
        current_state = self.state
        if current_state != previous_states.get(self):
            just_render_fn(self, *args, **kwargs)
            previous_states[self] = deepcopy(current_state)

    return wrapper


@contextmanager
def noop_context():
    # type: () -> Iterator[None]
    yield


class ReactiveInterface(Interface, GitCommand, Generic[T_state]):
    state: T_state
    subscribe_to: Set[str]

    def __init__(self, *args, **kwargs):
        self._lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def refresh_view_state(self):
        # type: () -> None
        raise NotImplementedError

    def update_state(self, data, then=None):
        # type: (...) -> None
        """Update internal view state and maybe invoke a callback.

        `data` can be a mapping or a callable ("thunk") which returns
        a mapping.

        Note: We invoke the "sink" without any arguments. TBC.
        """
        if callable(data):
            data = data()

        with self._lock:
            self.state.update(data)

        if callable(then):
            then()

    def render(self):
        # type: () -> None
        """Refresh view state and render."""
        self.refresh_view_state()
        self.just_render()

    # We check twice if a re-render is actually necessary because the state has grown
    # and invalidates when formatted relative dates change, t.i., too often.
    @distinct_until_state_changed                                             # <== 1st check data/state
    def just_render(self, keep_cursor_on_something=True):
        # type: (bool) -> None
        content, regions = self._render_template()
        if content == self.view.substr(sublime.Region(0, self.view.size())):  # <== 2nd check actual view content
            return

        ctx = self.keep_cursor_on_something() if keep_cursor_on_something else noop_context()
        with ctx:
            self.draw(self.title(), content, regions)

    def initial_state(self):
        # type: () -> Dict
        """Return the initial state of the view."""
        return {}

    def on_create(self):
        # type: () -> None
        self.state = self.initial_state()
        self._unsubscribe = store.subscribe(
            self.repo_path,
            self.subscribe_to,
            self.on_status_update
        )
        state = self.current_state()
        new_state = self._pick_subscribed_topics_from_store(state)
        self.update_state(new_state)

    def on_close(self):
        # type: () -> None
        self._unsubscribe()

    def on_status_update(self, _repo_path, state):
        # type: (...) -> None
        new_state = self._pick_subscribed_topics_from_store(state)
        self.update_state(new_state, then=self.just_render)

    def _pick_subscribed_topics_from_store(self, state):
        new_state = {}
        for topic in self.subscribe_to:
            try:
                new_state[topic] = state[topic]
            except KeyError:
                pass
        return new_state


def section(key):
    # type: (str) -> Callable[[RenderFn[T_R]], SectionFn[T_R]]
    def decorator(fn):
        # type: (RenderFn) -> SectionFn
        fn.key = key  # type: ignore[attr-defined]
        return cast(SectionFn, inject_state()(fn))
    return decorator


def inject_state():
    # type: () -> Callable[[FunctionReturning[T]], FunctionReturning[Union[T, str]]]
    def decorator(fn):
        # type: (FunctionReturning[T]) -> FunctionReturning[Union[T, str]]
        sig = inspect.signature(fn)
        keys = ordered_positional_args(sig)
        if "self" not in keys:
            return fn

        @wraps(fn)  # <- copies our key too! 🙏
        def decorated(self, *args, **kwargs):
            # type: (...) -> Union[T, str]
            b = sig.bind_partial(self, *args, **kwargs)
            given_args = b.arguments.keys()
            try:
                values = {key: self.state[key] for key in keys if key not in given_args}
            except KeyError:
                return ""
            else:
                kwargs.update(b.arguments)
                kwargs.update(values)
                return fn(**kwargs)
        return decorated
    return decorator


def ordered_positional_args(sig):
    # type: (inspect.Signature) -> List[str]
    return [
        name
        for name, parameter in sig.parameters.items()
        if parameter.default is inspect.Parameter.empty
    ]


def indent_by_2(text):
    return "\n".join(line[2:] for line in text.split("\n"))


def should_do_a_full_render(current, previous):
    # type: (AbstractSet[str], AbstractSet[str]) -> bool
    return bool(current - previous) or not previous


class gs_new_content_and_regions(TextCommand):
    current_region_names = set()  # type: AbstractSet[str]

    def run(self, edit, content, regions):
        # type: (object, str, Dict[str, Tuple[int, int]]) -> None
        def region_key(key):
            return "git_savvy_interface." + key

        def new_content_for_key(key) -> str:
            try:
                a, b = regions[key]
            except KeyError:
                return ""
            else:
                return content[a:b]

        if should_do_a_full_render(regions.keys(), self.current_region_names):
            replace_view_content(self.view, content)

        else:
            current_regions = [
                (region, key)
                for key in self.current_region_names | regions.keys()
                for region in self.view.get_regions(region_key(key))
            ]
            for region, key in sorted(current_regions, reverse=True):
                # For nested regions the actual content does not matter
                # as the outermost region/key has-it-all.
                # Skip these, as we would also need to read the updated
                # region via `get_regions` again.  (E.g. the outer region
                # shrinks with or when an inner region shrinks.)
                if any(r_.contains(region) for r_, _ in current_regions if r_ != region):
                    continue

                txt = new_content_for_key(key)
                # comparing the lengths is an optimization
                if len(region) != len(txt) or txt != self.view.substr(region):
                    replace_view_content(self.view, txt, region)

        for key, region_range in regions.items():
            self.view.add_regions(
                region_key(key),
                [region_from_tuple(region_range)],
                flags=sublime.RegionFlags.NO_UNDO
            )

        for key in self.current_region_names - regions.keys():
            self.view.erase_regions(region_key(key))

        self.current_region_names = regions.keys()

        if self.view.settings().get("git_savvy.interface"):
            self.view.run_command("gs_handle_vintageous")
            self.view.run_command("gs_handle_arrow_keys")


class gs_update_region(TextCommand):
    def run(self, edit, key, content):
        for region in self.view.get_regions(key):
            replace_view_content(self.view, content, region)


class collect_pre_run_handlers(type):
    def __init__(cls, cls_name, bases, attrs):  # type: ignore[misc]
        # type: (Type[InterfaceCommand], str, Tuple[object, ...], Dict[str, object]) -> None
        pre_run_handler = attrs.pop("pre_run", None)
        if pre_run_handler:
            cls._pre_run_handlers = [*cls._pre_run_handlers, pre_run_handler]  # type: ignore[has-type]


class InterfaceCommand(GsTextCommand, metaclass=collect_pre_run_handlers):
    interface: Interface
    _pre_run_handlers: List = []

    def run_(self, edit_token, args):
        try:
            for handler in self._pre_run_handlers:
                handler(self)
        except RuntimeError as e:
            flash(self.view, e.args[0])
            return
        return super().run_(edit_token, args)

    def pre_run(self) -> None:
        """Hook called before each `run`

        Raise a `RuntimeError` to abort the `run`.  Its message is
        presented in the status bar to the user.
        No traceback is logged for that exception to the console.

        Do not call `super().pre_run()` as this is handled automatically.
        """
        self.interface = ensure_interface_object(self.view)

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
    return list(unique_regions(flatten(view.lines(s) for s in multi_selector.get_selection(view))))


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
        interface = interfaces.get(view_id, None)
        if interface:
            interface.on_close()
            enqueue_on_worker(lambda: interfaces.pop(view_id))


class gs_interface_refresh(TextCommand):

    """
    Re-render GitSavvy interface view.
    """

    def run(self, edit):
        # type: (object) -> None
        interface = ensure_interface_object(self.view)
        if self.view.settings().get("git_savvy.update_view_in_a_blocking_manner"):
            try:
                interface.render()
            finally:
                self.view.settings().erase("git_savvy.update_view_in_a_blocking_manner")

        else:
            enqueue_on_worker(interface.render)


class gs_interface_toggle_help(TextCommand):

    """
    Toggle GitSavvy help.
    """

    def run(self, edit):
        current_help = bool(self.view.settings().get("git_savvy.help_hidden"))
        self.view.settings().set("git_savvy.help_hidden", not current_help)
        self.view.run_command("gs_interface_refresh")


class gs_interface_show_commit(TextCommand):
    def run(self, edit: sublime.Edit) -> None:
        view = self.view
        frozen_sel = list(multi_selector.get_selection(view))
        window = view.window()
        assert window

        for r in view.find_by_selector("constant.other.git-savvy.sha1"):
            for s in frozen_sel:
                for line in view.lines(s):
                    if line.a <= r.a < line.b:
                        window.run_command("gs_show_commit", {"commit_hash": view.substr(r)})


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
