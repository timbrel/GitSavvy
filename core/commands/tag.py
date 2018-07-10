import re
import sublime
from sublime_plugin import TextCommand

from ...common import util
from ..git_command import GitCommand
from ..ui_mixins.quick_panel import PanelActionMixin
from ..ui_mixins.input_panel import show_single_line_input_panel

RELEASE_REGEXP = re.compile(r"^([0-9A-Za-z-]*[A-Za-z-])?([0-9]+)\.([0-9]+)\.([0-9]+)(?:-([0-9A-Za-z-\.]*?)?([0-9]+))?$")

TAG_CREATE_PROMPT = "Enter tag:"
TAG_CREATE_MESSAGE = "Tag \"{}\" created."
TAG_CREATE_MESSAGE_PROMPT = "Enter message:"
TAG_PARSE_FAIL_MESSAGE = "The last tag cannot be parsed."


def smart_incremented_tag(tag, release_type):
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


class GsTagCreateCommand(TextCommand, GitCommand):

    """
    Through a series of panels, allow the user to add a tag and message.
    """

    def run(self, edit, tag_name=""):
        self.window = self.view.window()
        self.tag_name = tag_name
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        """
        Prompt the user for a tag name.
        """
        show_single_line_input_panel(TAG_CREATE_PROMPT, self.tag_name, self.on_entered_name)

    def on_entered_name(self, tag_name):
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
            throw_on_stderr=False
        )

        if not stdout:
            return util.log.panel("\"{}\" is not a valid tag name.".format(tag_name))

        self.tag_name = stdout.strip()[10:]
        show_single_line_input_panel(
            TAG_CREATE_MESSAGE_PROMPT,
            self.savvy_settings.get("default_tag_message").format(tag_name=tag_name),
            self.on_entered_message
        )

    def on_entered_message(self, message):
        """
        Create a tag with the specified tag name and message.
        """
        if not message:
            return

        self.git("tag", self.tag_name, "-F", "-", stdin=message)
        self.view.window().status_message(TAG_CREATE_MESSAGE.format(self.tag_name))
        util.view.refresh_gitsavvy(self.view)


class GsSmartTagCommand(PanelActionMixin, TextCommand, GitCommand):
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
        tag_name = None
        last_tag_name = self.get_last_local_tag()
        if last_tag_name:
            tag_name = smart_incremented_tag(last_tag_name, release_type)

        if not tag_name:
            sublime.message_dialog(TAG_PARSE_FAIL_MESSAGE)
            return

        self.view.run_command("gs_tag_create", {"tag_name": tag_name})
