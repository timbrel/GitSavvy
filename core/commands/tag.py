import re
from sublime_plugin import TextCommand

from ...common import util
from ..git_command import GitSavvyError
from ..ui_mixins.quick_panel import PanelActionMixin
from ..ui_mixins.input_panel import show_single_line_input_panel
from GitSavvy.core.base_commands import GsTextCommand
from ..ui__quick_panel import noop, show_actions_panel
from GitSavvy.core.utils import uprint, yes_no_switch


__all__ = (
    "gs_tag_create",
    "gs_smart_tag",
)


from typing import Literal, Optional
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


class gs_tag_create(GsTextCommand):

    """
    Through a series of panels, allow the user to add a tag and message.
    """

    def run(self, edit, tag_name="", target_commit=None):
        # type: (object, str, str) -> None
        window = self.view.window()
        if not window:
            return
        self.window = window
        self.target_commit = target_commit
        show_single_line_input_panel(TAG_CREATE_PROMPT, tag_name, self.on_entered_name)

    def on_entered_name(self, tag_name):
        # type: (str) -> None
        """
        After the user has entered a tag name, validate the tag name and prompt for
        a tag message.
        """
        if not tag_name:
            return

        stdout = self.git(
            "check-ref-format",
            "--normalize",
            "refs/tags/" + tag_name,
            throw_on_error=False
        )

        if not stdout:
            util.log.display_panel(
                self.window,
                "'{}' is not a valid tag name.".format(tag_name)
            )
            return None

        self.tag_name = stdout.strip()[10:]

        if MAYBE_SEMVER.search(tag_name) and self.savvy_settings.get("only_ask_to_annotate_versions"):
            show_single_line_input_panel(
                TAG_CREATE_MESSAGE_PROMPT,
                self.savvy_settings.get("default_tag_message").format(tag_name=tag_name),
                self.on_entered_message
            )
        else:
            self.on_entered_message()

    def on_entered_message(self, message=None, force=False):
        # type: (str, bool) -> None
        """
        Create a tag with the specified tag name and message.
        """
        try:
            if not message:
                self.git_throwing_silently("tag", yes_no_switch("--force", force), self.tag_name, self.target_commit)
            else:
                self.git_throwing_silently(
                    "tag", yes_no_switch("--force", force), self.tag_name, self.target_commit,
                    "-F", "-", stdin=message)
        except GitSavvyError as e:
            if TAG_ALREADY_EXISTS_MESSAGE.format(self.tag_name) in e.stderr and not force:
                def overwrite_action():
                    old_hash = self.git("rev-parse", self.tag_name).strip()
                    uprint(RECREATE_TAG_UNDO_MESSAGE.format(self.tag_name, old_hash))
                    self.on_entered_message(message, force=True)

                show_actions_panel(self.window, [
                    noop(f"Abort, a tag named '{self.tag_name}' already exists."),
                    (
                        f'Re-create the tag at {self.target_commit}.',
                        overwrite_action
                    )
                ])
                return

            else:
                e.show_error_panel()
                raise

        self.window.status_message(TAG_CREATE_MESSAGE.format(self.tag_name))
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

    def smart_tag(self, release_type):
        # type: (ReleaseTypes) -> None
        last_tag_name = self.get_last_local_semver_tag() or VERSION_ZERO
        tag_name = smart_incremented_tag(last_tag_name, release_type) or last_tag_name
        self.view.run_command("gs_tag_create", {"tag_name": tag_name})
