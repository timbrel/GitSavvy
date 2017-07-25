"""
Given the resource path to a Sublime theme file, generate a new
theme and allow the consumer to augment this theme and apply it
to a view.
"""

import os
from xml.etree import ElementTree

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

GLOBAL_STYLE_TEMPLATE = """
 <dict>
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

    def __init__(self, original_color_scheme):
        color_scheme_xml = sublime.load_resource(original_color_scheme)
        self.plist = ElementTree.XML(color_scheme_xml)
        self.global_style = self.find_global_style()
        self.styles = self.plist.find("./dict/array")

    def find_global_style(self):
        for style in self.plist.iterfind("./dict/array/dict"):
            if "scope" not in [t.text for t in style.iterfind("./key")]:
                return style
        return None

    def get_style_settings(self, style, key):
        settings = style.find("./dict")
        for i, entry in enumerate(settings):
            if entry.text == key and settings[i+1].tag == "string":
                return settings[i+1].text

    def set_style_settings(self, style, key, value):
        settings = style.find("./dict")
        for i, entry in enumerate(settings):
            if entry.text == key and settings[i+1].tag == "string":
                settings[i+1].text = value

    def get_global_settings(self, key):
        return self.get_style_settings(self.global_style, key)

    def set_global_settings(self, key, value):
        self.set_style_settings(self.global_style, key, value)

    def add_scoped_style(self, name, scope, **kwargs):
        """
        Add scope-specific styles to the theme.  A unique name should be provided
        as well as a scope corresponding to regions of text.  Any keyword arguments
        will be used as key and value for the newly-defined style.
        """
        properties = "".join(PROPERTY_TEMPLATE.format(key=k, value=v) for k, v in kwargs.items())
        new_style = STYLE_TEMPLATE.format(name=name, scope=scope, properties=properties)
        self.styles.append(ElementTree.XML(new_style))

    def set_global_style(self, **kwargs):
        """
        Remvoe the global style (if found) and restruct it from the arguments.
        """
        self.global_style = self.find_global_style()
        if self.global_style:
            self.styles.remove(self.global_style)
        properties = "".join(PROPERTY_TEMPLATE.format(key=k, value=v) for k, v in kwargs.items())
        new_style = GLOBAL_STYLE_TEMPLATE.format(properties=properties)
        self.styles.insert(0, ElementTree.XML(new_style))
        self.global_style = self.find_global_style()

    def remove_all_styles(self):
        styles = [s for s in self.styles]
        for style in styles:
            self.styles.remove(style)

    def get_new_theme_path(self, name):
        """
        Return the path to the theme relative to the Sublime packages directory.
        """
        if not os.path.exists(os.path.join(sublime.packages_path(), "User", "GitSavvy")):
            os.makedirs(os.path.join(sublime.packages_path(), "User", "GitSavvy"))

        path_in_packages = os.path.join("User",
                                        "GitSavvy",
                                        "GitSavvy.{}.hidden-tmTheme".format(name))

        return path_in_packages

    def apply_new_theme(self, name, target_view):
        """
        Save the transformed theme to disk and apply the transformed theme to the specified target view.
        """
        path_in_packages = self.get_new_theme_path(name)

        full_path = os.path.join(sublime.packages_path(), path_in_packages)

        with util.file.safe_open(full_path, "wb") as out_f:
            out_f.write(STYLES_HEADER.encode("utf-8"))
            out_f.write(ElementTree.tostring(self.plist, encoding="utf-8"))

        # Sublime expects `/`-delimited paths, even in Windows.
        theme_path = os.path.join("Packages", path_in_packages).replace("\\", "/")
        target_view.settings().set("color_scheme", theme_path)
