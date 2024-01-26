from collections import OrderedDict

from ..ui_mixins.input_panel import show_single_line_input_panel
from ..view import replace_view_content
from GitSavvy.core.base_commands import GsWindowCommand


from typing import Iterable, List


REF_PROMPT = "Ref or commit hash:"
CHANGELOG_TEMPLATE = """Changes since {ref}:
{changes}"""

GROUP_TEMPLATE = """
  {group}:
{messages}
"""


class GsGenerateChangeLogCommand(GsWindowCommand):

    """
    Prompt the user for a ref or commit hash.  Once provided,
    compile all commit summaries between provided ref and HEAD
    and display in new scratch view.
    """

    def run(self):
        show_single_line_input_panel(REF_PROMPT, self.get_last_local_semver_tag() or "", self.on_done)

    def on_done(self, ref):
        merge_entries = self.log(
            start_end=(ref, "HEAD"),
            first_parent=True,
            merges=True
        )

        ancestor = {}
        for merge in merge_entries:
            merge_commits = self.commits_of_merge(merge.long_hash)
            if len(merge_commits) > 1:
                for entry in merge_commits:
                    ancestor[entry] = merge.short_hash

        entries = self.log(
            start_end=(ref, "HEAD"),
            no_merges=True,
            topo_order=True,
            reverse=True
        )

        contributors = set()
        messages = []
        for entry in entries:
            contributors.add(entry.author)
            if entry.long_hash in ancestor:
                messages.append("{} (Merge {})".format(entry.summary, ancestor[entry.long_hash]))
            elif entry.raw_body.find('BREAKING:') >= 0:
                pos_start = entry.raw_body.find('BREAKING:')
                key_length = len('BREAKING:')
                indented_sub_msg = ('\n\t\t' + ' ' * key_length + ' ').join(entry.raw_body[pos_start:].split('\n'))
                messages.append("{}\n\t\t{})".format(entry.summary, indented_sub_msg))
            else:
                messages.append(entry.summary)

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
        replace_view_content(view, changelog)

    def get_message_groups(self, messages):
        # type: (List[str]) -> OrderedDict[str, Iterable[str]]
        grouped_msgs = OrderedDict()  # type: OrderedDict[str, List[str]]
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

        return grouped_msgs  # type: ignore[return-value]
