from collections import OrderedDict

import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand

REF_PROMPT = "Ref or commit hash:"

CHANGELOG_TEMPLATE = """Changes since {ref}:
{changes}"""

GROUP_TEMPLATE = """
  {group}:
{messages}
"""


class GsGenerateChangeLogCommand(WindowCommand, GitCommand):

    """
    Prompt the user for a ref or commit hash.  Once provided,
    compile all commit summaries between provided ref and HEAD
    and display in new scratch view.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.window.show_input_panel(REF_PROMPT, "", self.on_done, None, None)

    def on_done(self, ref):
        stdout = self.git(
            "log",
            "--no-merges",
            "--pretty=format:%an%x00%s",
            "{}..HEAD".format(ref)
            )

        contributors = set()
        messages = []
        for line in stdout.split("\n"):
            if line:
                contributor, message = line.strip().split("\x00")
                contributors.add(contributor)
                messages.append(message)

        msg_groups = self.get_message_groups(messages)
        msg_groups["Contributors"] = contributors

        group_strings = (
            GROUP_TEMPLATE.format(
                group=group_name,
                messages="\n".join("   - " + message for message in messages)
                )
            for group_name, messages in msg_groups.items()
            )

        changelog = CHANGELOG_TEMPLATE.format(
            ref=ref,
            changes="".join(group_strings)
            )

        view = self.window.new_file()
        view.set_scratch(True)
        view.run_command("gs_replace_view_text", {
            "text": changelog,
            "nuke_cursors": True
            })

    @staticmethod
    def get_message_groups(messages):
        grouped_msgs = OrderedDict()

        for message in messages:
            first_colon = message.find(":")
            first_space = message.find(" ")

            # YES "fix: some summary info"
            # YES "feature: some summary info"
            #  NO "feature some summary info"
            #  NO " some summary info"
            if first_space > 0 and first_space == first_colon + 1:
                group = message[:first_colon]
                message = message[first_space + 1:]
            else:
                group = "Other"

            if group not in grouped_msgs:
                grouped_msgs[group] = []

            grouped_msgs[group].append(message)

        return grouped_msgs
