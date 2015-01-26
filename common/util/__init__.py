import sublime
from plistlib import readPlistFromBytes

from .parse_diff import parse_diff

syntax_file_map = {}


def move_cursor(view, line_no, char_no):
    # Line numbers are one-based, rows are zero-based.
    line_no -= 1

    # Negative line index counts backwards from the last line.
    if line_no < 0:
        last_line, _ = view.rowcol(view.size())
        line_no = last_line + line_no + 1

    pt = view.text_point(line_no, char_no)
    view.sel().clear()
    view.sel().add(sublime.Region(pt))
    view.show(pt)


def get_lines_from_regions(view, regions):
    full_line_regions = (view.full_line(region) for region in regions)
    return [line for region in full_line_regions for line in view.substr(region).split("\n")]


def determine_syntax_files():
    syntax_files = sublime.find_resources("*.tmLanguage")
    for syntax_file in syntax_files:
        print("trying:", syntax_file)
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
    extension = filename.split(".")[-1]
    try:
        # Return last syntax file applicable to this extension.
        return syntax_file_map[extension][-1]
    except:
        return None
