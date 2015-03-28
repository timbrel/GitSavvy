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
    syntaxes = syntax_file_map.get(filename, None) or syntax_file_map.get(extension, None)
    return syntaxes[-1] if syntaxes else "Packages/Text/Plain text.tmLanguage"


def get_file_extension(filename):
    period_delimited_segments = filename.split(".")
    return "" if len(period_delimited_segments) < 2 else period_delimited_segments[-1]
