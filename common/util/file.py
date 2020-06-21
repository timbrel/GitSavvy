from collections import defaultdict
from contextlib import contextmanager
import os
import plistlib
import re
import threading
import yaml

import sublime


MYPY = False
if MYPY:
    from typing import DefaultDict, List, Optional


if 'syntax_file_map' not in globals():
    syntax_file_map = defaultdict(list)  # type: DefaultDict[str, List[str]]

if 'determine_syntax_thread' not in globals():
    determine_syntax_thread = None


def determine_syntax_files():
    # type: () -> None
    global determine_syntax_thread
    if not syntax_file_map:
        determine_syntax_thread = threading.Thread(
            target=_determine_syntax_files)
        determine_syntax_thread.start()


def try_parse_for_file_extensions(text):
    # type: (str) -> Optional[List[str]]
    match = re.search(r"^file_extensions:\n((.*\n)+?)^(?=\w)", text, re.M)
    if match:
        return _try_yaml_parse(match.group(0))
    return _try_yaml_parse(text)


def _try_yaml_parse(text):
    # type: (str) -> Optional[List[str]]
    try:
        return yaml.safe_load(text)["file_extensions"]
    except Exception:
        return None


def _determine_syntax_files():
    # type: () -> None
    handle_tm_language_files()
    handle_sublime_syntax_files()


def handle_tm_language_files():
    # type: () -> None
    syntax_files = sublime.find_resources("*.tmLanguage")
    for syntax_file in syntax_files:
        try:
            resource = sublime.load_binary_resource(syntax_file)
        except Exception:
            print("GitSavvy: could not load {}".format(syntax_file))
            continue

        try:
            extensions = plistlib.readPlistFromBytes(resource).get("fileTypes", [])
        except Exception:
            print("GitSavvy: could not parse {}".format(syntax_file))
            continue

        for extension in extensions:
            syntax_file_map[extension].append(syntax_file)


def handle_sublime_syntax_files():
    # type: () -> None
    syntax_files = sublime.find_resources("*.sublime-syntax")
    for syntax_file in syntax_files:
        try:
            resource = sublime.load_resource(syntax_file)
        except Exception:
            print("GitSavvy: could not load {}".format(syntax_file))
            continue

        for extension in try_parse_for_file_extensions(resource) or []:
            syntax_file_map[extension].append(syntax_file)


def guess_syntax_for_file(window, filename):
    # type: (sublime.Window, str) -> str
    view = window.find_open_file(filename)
    if view:
        syntax = view.settings().get("syntax")
        remember_syntax_choice(filename, syntax)
        return syntax
    return get_syntax_for_file(filename)


def remember_syntax_choice(filename, syntax):
    # type: (str, str) -> None
    registered_syntaxes = (
        syntax_file_map.get(filename, [])
        or syntax_file_map.get(get_file_extension(filename), [])
    )
    if syntax in registered_syntaxes:
        registered_syntaxes.remove(syntax)
    registered_syntaxes.append(syntax)


def get_syntax_for_file(filename, default="Packages/Text/Plain text.tmLanguage"):
    # type: (str, str) -> str
    if not determine_syntax_thread or determine_syntax_thread.is_alive():
        return default
    syntaxes = (
        syntax_file_map.get(filename, [])
        or syntax_file_map.get(get_file_extension(filename), [])
        or [default]
    )
    return syntaxes[-1]


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
