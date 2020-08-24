"""
Given the resource path to a Sublime theme file, generate a new
theme and allow the consumer to augment this theme and apply it
to a view.
"""

import os
from xml.etree import ElementTree
from collections import OrderedDict

import sublime
from . import util

STYLES_HEADER = """
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
"""

STYLE_TEMPLATE = """
 <dict>
    <key>name</key>
    <string>{name}</string>
    <key>scope</key>
    <string>{scope}</string>
    <key>settings</key>
    <dict>
{properties}
    </dict>
</dict>
"""

PROPERTY_TEMPLATE = """
        <key>{key}</key>
        <string>{value}</string>
"""


class ThemeGenerator():
    """
    Given the path to a `.tmTheme` file, parse it, allow transformations
    on the data, save it, and apply the transformed theme to a view.
    """

    hidden_theme_extension = None  # type: str

    @staticmethod
    def for_view(view):
        # type: (sublime.View) -> ThemeGenerator
        color_scheme = view.settings().get('color_scheme')
        if color_scheme.endswith(".tmTheme"):
            return XMLThemeGenerator(color_scheme)
        else:
            return JSONThemeGenerator(color_scheme)

    def __init__(self, original_color_scheme):
        self._dirty = False
        try:
            self.color_scheme_string = sublime.load_resource(original_color_scheme)
        except IOError:
            # then use sublime.find_resources
            paths = sublime.find_resources(original_color_scheme)
            if not paths:
                raise IOError("{} cannot be found".format(original_color_scheme))
            for path in paths:
                if path.startswith("Packages/User/"):
                    # load user specfic theme first
                    self.color_scheme_string = sublime.load_resource(path)
                    break
            self.color_scheme_string = sublime.load_resource(paths[0])

    def get_theme_name(self, name):
        return "GitSavvy.{}.{}".format(name, self.hidden_theme_extension)

    def get_theme_path(self, name):
        """
        Save the transformed theme to disk and return the path to that theme,
        relative to the Sublime packages directory.
        """
        if not os.path.exists(os.path.join(sublime.packages_path(), "User", "GitSavvy")):
            os.makedirs(os.path.join(sublime.packages_path(), "User", "GitSavvy"))

        return os.path.join("User", "GitSavvy", self.get_theme_name(name))

    def add_scoped_style(self, name, scope, **kwargs):
        """
        Add scope-specific styles to the theme.  A unique name should be provided
        as well as a scope corresponding to regions of text.  Any keyword arguments
        will be used as key and value for the newly-defined style.
        """
        if scope in self.color_scheme_string:
            return

        self._dirty = True
        self._add_scoped_style(name, scope, **kwargs)

    def _add_scoped_style(self, name, scope, **kwargs):
        raise NotImplementedError

    def write_new_theme(self, name):
        """
        Write the new theme on disk.
        """
        pass

    def apply_new_theme(self, name, target_view):
        """
        Apply the transformed theme to the specified target view.
        """
        if not self._dirty:
            return

        self.write_new_theme(name)

        path_in_packages = self.get_theme_path(name)

        # Sublime expects `/`-delimited paths, even in Windows.
        theme_path = os.path.join("Packages", path_in_packages).replace("\\", "/")
        try_apply_theme(target_view, theme_path)


class XMLThemeGenerator(ThemeGenerator):
    """
    A theme generator for the vintage syntax `.tmTheme`
    """

    hidden_theme_extension = "hidden-tmTheme"

    def __init__(self, original_color_scheme):
        super().__init__(original_color_scheme)
        self.plist = ElementTree.XML(self.color_scheme_string)
        styles = self.plist.find("./dict/array")
        assert styles
        self.styles = styles

    def _add_scoped_style(self, name, scope, **kwargs):
        properties = "".join(PROPERTY_TEMPLATE.format(key=k, value=v) for k, v in kwargs.items())
        new_style = STYLE_TEMPLATE.format(name=name, scope=scope, properties=properties)
        self.styles.append(ElementTree.XML(new_style))

    def write_new_theme(self, name):
        full_path = os.path.join(sublime.packages_path(), self.get_theme_path(name))

        with util.file.safe_open(full_path, "wb", buffering=0) as out_f:
            out_f.write(STYLES_HEADER.encode("utf-8"))
            out_f.write(ElementTree.tostring(self.plist, encoding="utf-8"))


class JSONThemeGenerator(ThemeGenerator):
    """
    A theme generator for the new syntax `.sublime-color-scheme`
    """

    hidden_theme_extension = "hidden-color-scheme"

    def __init__(self, original_color_scheme):
        super().__init__(original_color_scheme)
        self.dict = OrderedDict(sublime.decode_value(self.color_scheme_string))

    def _add_scoped_style(self, name, scope, **kwargs):
        new_rule = OrderedDict([("name", name), ("scope", scope)])
        for (k, v) in kwargs.items():
            new_rule[k] = v
        self.dict["rules"].insert(0, new_rule)

    def write_new_theme(self, name):
        full_path = os.path.join(sublime.packages_path(), self.get_theme_path(name))

        with util.file.safe_open(full_path, "wb", buffering=0) as out_f:
            out_f.write(sublime.encode_value(self.dict, pretty=True).encode("utf-8"))


def try_apply_theme(view, theme_path, tries=0):
    """ Safly apply new theme as color_scheme. """
    try:
        sublime.load_resource(theme_path)
    except Exception:
        if tries >= 8:
            print(
                'GitSavvy: The theme {} is not ready to load. Maybe restart to get colored '
                'highlights.'.format(theme_path)
            )
            return

        delay = (pow(2, tries) - 1) * 10
        sublime.set_timeout_async(lambda: try_apply_theme(view, theme_path, tries + 1), delay)
        return

    view.settings().set("color_scheme", theme_path)
