from __future__ import annotations

from datetime import datetime
from functools import partial
import re
from string import Formatter
import sublime

from . import ref_undo
from ...common import util
from ..git_command import GitSavvyError
from ..git_mixins.tags import is_version_tag
from ..ui_mixins.input_panel import show_single_line_input_panel
from GitSavvy.core.base_commands import (
    call_with_wanted_args,
    Args, GsCommand, GsWindowCommand, Kont, std_undo_owner)
from ..ui__quick_panel import ActionType, noop, show_actions_panel
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

TAG_CREATE_PROMPT = "Enter tag:"
TAG_CREATE_MESSAGE = "Tag \"{}\" created."
TAG_CREATE_MESSAGE_PROMPT = "Enter message:"
TAG_ALREADY_EXISTS_MESSAGE = "tag '{0}' already exists"
RECREATE_TAG_UNDO_MESSAGE = """\
GitSavvy: Re-created tag '{0}', in case you want to undo, run:
  $ git tag --force {0} {1}
"""
VERSION_ZERO = "v0.0.0"
DEFAULT_CALENDAR_VERSION_STYLE = "{year}.{month}.{day}.{hour}.{minute}.{second}"
SHORT_CALENDAR_VERSION_STYLE = "{year}.{month}.{day}"
CALENDAR_VERSION_FIELDS = {"year", "month", "day", "hour", "minute", "second"}


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
        or is_version_tag(tag_name)
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
            previous_hash = self.resolve_tag(tag_name, lenient=True)

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
                    previous_hash = self.resolve_tag(tag_name)
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


class gs_smart_tag(GsWindowCommand):
    """
    Displays a panel of possible smart tag options, based on the choice,
    tag the current commit with the corresponding tagname.
    """
    last_selected_items: dict[str, int] = {}

    def run(self, version_style: str | None = None) -> None:
        if not version_style:
            version_style = self.get_local_tags().version_style

        actions = (
            self.calendar_actions()
            if version_style == "calendar" else
            self.semver_actions()
        )
        show_actions_panel(
            self.window,
            self.with_selection_storage(version_style, actions),
            select=self.last_selected_item(version_style, actions)
        )

    def with_selection_storage(
        self,
        version_style: str,
        actions: list[ActionType]
    ) -> list[ActionType]:
        return [
            (description, partial(self.run_and_remember, version_style, idx, action))
            for idx, (description, action) in enumerate(actions)
        ]

    def last_selected_item(self, version_style: str, actions: list[ActionType]) -> int:
        return min(self.last_selected_items.get(version_style, -1), len(actions) - 1)

    def run_and_remember(
        self,
        version_style: str,
        idx: int,
        action: Callable[[], None]
    ) -> None:
        self.last_selected_items[version_style] = idx
        action()

    def calendar_actions(self) -> list[ActionType]:
        style = self.get_calendar_version_style()
        primary, secondary = calendar_version_options(style)
        return [
            ("Create '{}'".format(primary), partial(self.create_tag, primary)),
            ("Create '{}'".format(secondary), partial(self.create_tag, secondary)),
            ("Edit the tag name", partial(self.edit_tag_name, primary)),
        ]

    def semver_actions(self) -> list[ActionType]:
        return [
            ("patch", partial(self.smart_tag, "patch")),
            ("minor", partial(self.smart_tag, "minor")),
            ("major", partial(self.smart_tag, "major")),
            ("prerelease", partial(self.smart_tag, "prerelease")),
            ("prepatch", partial(self.smart_tag, "prepatch")),
            ("preminor", partial(self.smart_tag, "preminor")),
            ("premajor", partial(self.smart_tag, "premajor")),
        ]

    def get_calendar_version_style(self) -> str:
        style = self.savvy_settings.get("calendar_version_style")
        if not calendar_version_style_is_valid(style):
            print(
                f"calendar_version_style is invalid. "
                f"falling back to '{DEFAULT_CALENDAR_VERSION_STYLE}'"
            )
            return DEFAULT_CALENDAR_VERSION_STYLE
        return style

    def smart_tag(self, release_type: ReleaseTypes) -> None:
        last_tag_name = self.get_last_local_semver_tag() or VERSION_ZERO
        tag_name = smart_incremented_tag(last_tag_name, release_type) or last_tag_name
        self.edit_tag_name(tag_name)

    def create_tag(self, tag_name: str) -> None:
        self.window.run_command("gs_create_tag", {"tag_name": tag_name})

    def edit_tag_name(self, suggested_name: str) -> None:
        self.window.run_command("gs_create_tag", {"suggested_name": suggested_name})


def calendar_version_style_is_valid(style: object) -> bool:
    if not isinstance(style, str) or not style:
        return False

    try:
        fields = [field for _, field, _, _ in Formatter().parse(style) if field]
    except ValueError:
        return False

    if not fields or any(field not in CALENDAR_VERSION_FIELDS for field in fields):
        return False

    return True


def calendar_version_options(
    primary_style: str,
    now: datetime | None = None
) -> tuple[str, str]:
    now = now or datetime.now()
    primary = calendar_version(primary_style, now)
    secondary_style = (
        DEFAULT_CALENDAR_VERSION_STYLE
        if primary_style == SHORT_CALENDAR_VERSION_STYLE
        else SHORT_CALENDAR_VERSION_STYLE
    )
    return primary, calendar_version(secondary_style, now)


def calendar_version(style: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    try:
        return style.format(**calendar_version_fields(now))
    except (AttributeError, IndexError, KeyError, ValueError):
        return DEFAULT_CALENDAR_VERSION_STYLE.format(**calendar_version_fields(now))


def calendar_version_fields(now: datetime) -> dict[str, str]:
    return {
        "year": f"{now.year:04}",
        "month": f"{now.month:02}",
        "day": f"{now.day:02}",
        "hour": f"{now.hour:02}",
        "minute": f"{now.minute:02}",
        "second": f"{now.second:02}"
    }
