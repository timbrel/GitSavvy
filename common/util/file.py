import sublime
import threading
import yaml


if 'syntax_file_map' not in globals():
    syntax_file_map = {}

if 'determine_syntax_thread' not in globals():
    determine_syntax_thread = None


def determine_syntax_files():
    global determine_syntax_thread
    if not syntax_file_map:
        determine_syntax_thread = threading.Thread(
            target=_determine_syntax_files)
        determine_syntax_thread.start()


def _determine_syntax_files():
    syntax_files = sublime.find_resources("*.sublime-syntax")
    for syntax_file in syntax_files:
        try:
            # Use `sublime.load_resource`, in case Package is `*.sublime-package`.
            resource = sublime.load_resource(syntax_file)
            for extension in yaml.load(resource)["file_extensions"]:
                if extension not in syntax_file_map:
                    syntax_file_map[extension] = []
                extension_list = syntax_file_map[extension]
                extension_list.append(syntax_file)
        except:
            continue


def get_syntax_for_file(filename):
    if not determine_syntax_thread or determine_syntax_thread.is_alive():
        return "Packages/Text/Plain text.tmLanguage"
    extension = get_file_extension(filename)
    syntaxes = syntax_file_map.get(filename, None) or syntax_file_map.get(extension, None)
    return syntaxes[-1] if syntaxes else "Packages/Text/Plain text.tmLanguage"


def get_file_extension(filename):
    period_delimited_segments = filename.split(".")
    return "" if len(period_delimited_segments) < 2 else period_delimited_segments[-1]
