import sublime
from plistlib import readPlistFromBytes


syntax_file_map = {}


def determine_syntax_files():
    syntax_files = sublime.find_resources("*.tmLanguage")
    for syntax_file in syntax_files:
        try:
            # Use `sublime.load_resource`, in case Package is `*.sublime-package`.
            resource = sublime.load_resource(syntax_file)
            plist = readPlistFromBytes(bytearray(resource, encoding="utf-8"))
            for extension in plist["fileTypes"]:
                if extension not in syntax_file_map:
                    syntax_file_map[extension] = []
                extension_list = syntax_file_map[extension]
                extension_list.append(syntax_file)
        except:
            continue


def get_syntax_for_file(filename):
    extension = get_file_extension(filename)
    try:
        # Return last syntax file applicable to this extension.
        return syntax_file_map[extension][-1]
    except KeyError:
        pass
    return "Packages/Text/Plain text.tmLanguage"


def get_file_extension(filename):
    period_delimited_segments = filename.split(".")
    return "" if len(period_delimited_segments) < 2 else period_delimited_segments[-1]
