import sublime
import threading
import yaml
import os
from contextlib import contextmanager


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
            for extension in yaml.safe_load(resource)["file_extensions"]:
                if extension not in syntax_file_map:
                    syntax_file_map[extension] = []
                extension_list = syntax_file_map[extension]
                extension_list.append(syntax_file)
        except Exception:
            continue


def get_syntax_for_file(filename):
    if not determine_syntax_thread or determine_syntax_thread.is_alive():
        return "Packages/Text/Plain text.tmLanguage"
    extension = get_file_extension(filename)
    syntaxes = syntax_file_map.get(filename, None) or syntax_file_map.get(extension, None)
    return syntaxes[-1] if syntaxes else "Packages/Text/Plain text.tmLanguage"


def get_file_extension(filename):
    # type: (str) -> str
    return os.path.splitext(filename)[1][1:]


def get_file_contents_binary(repo_path, file_path):
    """
    Given an absolute file path, return the binary contents of that file
    as a string.
    """
    file_path = os.path.join(repo_path, file_path)
    with safe_open(file_path, "rb") as f:
        binary = f.read()
        binary = binary.replace(b"\r\n", b"\n")
        binary = binary.replace(b"\r", b"")
        return binary


def get_file_contents(repo_path, file_path):
    """
    Given an absolute file path, return the text contents of that file
    as a string.
    """
    binary = get_file_contents_binary(repo_path, file_path)
    try:
        return binary.decode('utf-8')
    except UnicodeDecodeError:
        return binary.decode('latin-1')


@contextmanager
def safe_open(filename, mode, *args, **kwargs):
    try:
        with open(filename, mode, *args, **kwargs) as file:
            yield file
    except PermissionError as e:
        sublime.ok_cancel_dialog("GitSavvy could not access file: \n{}".format(e))
        raise e
    except OSError as e:
        sublime.ok_cancel_dialog("GitSavvy encountered an OS error: \n{}".format(e))
        raise e
