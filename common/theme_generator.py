"""
Given the resource path to a Sublime theme file, generate a new
theme and allow the consumer to augment this theme and apply it
to a view.
"""

import os
from xml.etree import ElementTree

import sublime


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

    def __init__(self, original_color_scheme):
        color_scheme_xml = sublime.load_resource(original_color_scheme)
        self.plist = ElementTree.XML(color_scheme_xml)
        self.styles = self.plist.find("./dict/array")

    def add_scoped_style(self, name, scope, **kwargs):
        """
        Add scope-specific styles to the theme.  A unique name should be provided
        as well as a scope corresponding to regions of text.  Any keyword arguments
        will be used as key and value for the newly-defined style.
        """
        properties = "".join(PROPERTY_TEMPLATE.format(key=k, value=v) for k, v in kwargs.items())
        new_style = STYLE_TEMPLATE.format(name=name, scope=scope, properties=properties)
        self.styles.append(ElementTree.XML(new_style))

    def get_new_theme_path(self, name):
        """
        Save the transformed theme to disk and return the path to that theme,
        relative to the Sublime packages directory.
        """
        if not os.path.exists(os.path.join(sublime.packages_path(), "User", "GitSavvy")):
            os.makedirs(os.path.join(sublime.packages_path(), "User", "GitSavvy"))

        path_in_packages = os.path.join("User",
                                        "GitSavvy",
                                        "GitSavvy.{}.tmTheme".format(name))

        full_path = os.path.join(sublime.packages_path(), path_in_packages)

        with open(full_path, "wb") as out_f:
            out_f.write(STYLES_HEADER.encode("utf-8"))
            out_f.write(ElementTree.tostring(self.plist, encoding="utf-8"))

        return path_in_packages

    def apply_new_theme(self, name, target_view):
        """
        Apply the transformed theme to the specified target view.
        """
        path_in_packages = self.get_new_theme_path(name)

        # Sublime expects `/`-delimited paths, even in Windows.
        theme_path = os.path.join("Packages", path_in_packages).replace("\\", "/")
        target_view.settings().set("color_scheme", theme_path)
