from __future__ import annotations

import re
import sublime
from sublime_plugin import TextCommand

from . import ref_undo
from ...common import util
from ..git_command import GitSavvyError
from ..ui_mixins.quick_panel import PanelActionMixin
from ..ui_mixins.input_panel import show_single_line_input_panel
from GitSavvy.core.base_commands import (
    call_with_wanted_args,
    Args, GsCommand, GsWindowCommand, Kont, std_undo_owner)
from ..ui__quick_panel import noop, show_actions_panel
from GitSavvy.core.utils import just, uprint, yes_no_switch
from GitSavvy.core.types import CommitHash


__all__ = (
    "gs_create_tag",
    "gs_smart_tag",
)


from typing import Callable, Literal, Optional
ReleaseTypes = Literal[
    "patch",
    "minor",
    "major",
    "prerelease",
    "prepatch",
    "preminor",
    "premajor"]


RELEASE_REGEXP = re.compile(r"^([0-9A-Za-z-]*[A-Za-z-])?([0-9]+)\.([0-9]+)\.([0-9]+)(?:-([0-9A-Za-z-\.]*?)?([0-9]+))?$")
MAYBE_SEMVER = re.compile(r"\d+\.\d+(\.\d+)?")

TAG_CREATE_PROMPT = "Enter tag:"
TAG_CREATE_MESSAGE = "Tag \"{}\" created."
TAG_CREATE_MESSAGE_PROMPT = "Enter message:"
TAG_ALREADY_EXISTS_MESSAGE = "tag '{0}' already exists"
RECREATE_TAG_UNDO_MESSAGE = """\
GitSavvy: Re-created tag '{0}', in case you want to undo, run:
  $ git tag --force {0} {1}
"""
VERSION_ZERO = "v0.0.0"


def smart_incremented_tag(tag, release_type):
    # type: (str, ReleaseTypes) -> Optional[str]
    """
    Automatic increment of a given tag depending on the type of release.
    """

    m = RELEASE_REGEXP.match(tag)
    if m:
        prefix, major, minor, patch, prefix2, prerelease = m.groups()
        prefix = "" if not prefix else prefix
        prefix2 = "" if not prefix2 else prefix2

        if release_type == "premajor" \
                or (not prerelease and release_type == "major") \
                or (prerelease and release_type == "major" and (minor != "0" or patch != "0")):
            major = str(int(major) + 1)
            minor = patch = "0"
            prerelease = None
        elif release_type == "preminor" \
                or (not prerelease and release_type == "minor") \
                or (prerelease and release_type == "minor" and patch != "0"):
            minor = str(int(minor) + 1)
            patch = "0"
            prerelease = None
        elif release_type == "prepatch" \
                or (not prerelease and release_type == "prerelease") \
                or (not prerelease and release_type == "patch"):
            patch = str(int(patch) + 1)
            prerelease = None

        if "pre" in release_type[0:3]:
            prerelease = str(int(prerelease) + 1) if prerelease else "0"
            return prefix + major + "." + minor + "." + patch + "-" + prefix2 + prerelease
        else:
            return prefix + major + "." + minor + "." + patch

    return None


def default_tag_message(message_template: str, tag_name: str) -> str:
    if tag_name[:1].lower() == "v":
        message_template = message_template.replace("v{tag_name}", "{tag_name}")
        message_template = message_template.replace("V{tag_name}", "{tag_name}")

    return message_template.format(tag_name=tag_name)


def ask_for_tag_name(
    caption: str = TAG_CREATE_PROMPT,
    initial_text: Callable[..., str] = just("")
):
    def handler(cmd: GsCommand, args: Args, done: Kont, initial_text_: str = "") -> None:
        def on_done(tag_name):
            if not tag_name:
                return None

            normalized_tag_name = normalize_tag_name(cmd, tag_name)
            if not normalized_tag_name:
                util.log.display_panel(
                    cmd.window,
                    "'{}' is not a valid tag name.".format(tag_name)
                )
                handler(cmd, args, done, initial_text_=tag_name)
                return None

            done(normalized_tag_name)

        show_single_line_input_panel(
            caption,
            initial_text_ or call_with_wanted_args(initial_text, args),
            on_done
        )
    return handler


def ask_for_tag_message():
    def handler(cmd: GsCommand, args: Args, done: Kont) -> None:
        tag_name = args["tag_name"]
        if should_ask_for_tag_message(cmd, tag_name):
            show_single_line_input_panel(
                TAG_CREATE_MESSAGE_PROMPT,
                default_tag_message(cmd.savvy_settings.get("default_tag_message"), tag_name),
                done
            )
        else:
            done(None)
    return handler


def should_ask_for_tag_message(cmd: GsCommand, tag_name: str) -> bool:
    return (
        not cmd.savvy_settings.get("only_ask_to_annotate_versions")
        or bool(MAYBE_SEMVER.search(tag_name))
    )


def normalize_tag_name(cmd: GsCommand, tag_name: str) -> str | None:
    stdout = cmd.git(
        "check-ref-format",
        "--normalize",
        "refs/tags/" + tag_name,
        throw_on_error=False
    )
    return stdout.strip()[10:] if stdout else None


class gs_create_tag(GsWindowCommand):
    """
    Through a series of panels, allow the user to add a tag and message.
    """

    defaults = {
        "tag_name": ask_for_tag_name(
            initial_text=lambda suggested_name="": suggested_name
        ),
        "tag_message": ask_for_tag_message(),
        "undo_owner": std_undo_owner,
    }

    def run(
        self,
        tag_name: str,
        tag_message: str | None,
        undo_owner: sublime.ViewId,
        target_commit: str | None = None,
        force: bool = False,
        suggested_name: str = "",
        previous_hash: tuple[CommitHash, CommitHash] | None = None
    ) -> None:
        if force and previous_hash is None:
            if previous_tag_ref_hash := self.resolve(
                f"refs/tags/{tag_name}",
                on_error="ignore"
            ):
                if previous_tag_deref_hash := self.resolve(
                    f"refs/tags/{tag_name}^{{}}",
                    on_error="ignore"
                ):
                    previous_hash = (previous_tag_ref_hash, previous_tag_deref_hash)

        try:
            if not tag_message:
                self.git_throwing_silently(
                    "tag", yes_no_switch("--force", force), tag_name, target_commit)
            else:
                self.git_throwing_silently(
                    "tag", yes_no_switch("--force", force), tag_name, target_commit,
                    "-F", "-", stdin=tag_message)
        except GitSavvyError as e:
            if TAG_ALREADY_EXISTS_MESSAGE.format(tag_name) in e.stderr and not force:
                def overwrite_action():
                    previous_hash = (
                        self.resolve(f"refs/tags/{tag_name}"),
                        self.resolve(f"refs/tags/{tag_name}^{{}}")
                    )
                    uprint(RECREATE_TAG_UNDO_MESSAGE.format(
                        tag_name,
                        previous_hash[1]
                    ))

                    self.window.run_command("gs_create_tag", {
                        "tag_name": tag_name,
                        "tag_message": tag_message,
                        "target_commit": target_commit,
                        "force": True,
                        "previous_hash": previous_hash,
                        "undo_owner": undo_owner,
                    })

                show_actions_panel(self.window, [
                    noop(f"Abort, a tag named '{tag_name}' already exists."),
                    (
                        f'Re-create the tag at {target_commit or "HEAD"}.',
                        overwrite_action
                    )
                ])
                return

            else:
                e.show_error_panel()
                raise

        if force and previous_hash:
            ref_undo.add_tag_undo(
                self,
                tag_name,
                *previous_hash,
                undo_owner
            )

        self.window.status_message(TAG_CREATE_MESSAGE.format(tag_name))
        util.view.refresh_gitsavvy_interfaces(self.window)


class gs_smart_tag(PanelActionMixin, TextCommand):
    """
    Displays a panel of possible smart tag options, based on the choice,
    tag the current commit with the corresponding tagname.
    """

    async_action = True
    default_actions = [
        ["smart_tag", "patch", ("patch", )],
        ["smart_tag", "minor", ("minor", )],
        ["smart_tag", "major", ("major", )],
        ["smart_tag", "prerelease", ("prerelease", )],
        ["smart_tag", "prepatch", ("prepatch", )],
        ["smart_tag", "preminor", ("preminor", )],
        ["smart_tag", "premajor", ("premajor", )],
    ]

    def smart_tag(self, release_type: ReleaseTypes) -> None:
        last_tag_name = self.get_last_local_semver_tag() or VERSION_ZERO
        tag_name = smart_incremented_tag(last_tag_name, release_type) or last_tag_name
        window = self.view.window()
        if not window:
            return
        window.run_command("gs_create_tag", {"suggested_name": tag_name})
