import sublime
from sublime_plugin import WindowCommand
from webbrowser import open as open_in_browser
# import urllib

from ...core.git_command import GitCommand
from ...core.ui_mixins.quick_panel import show_paginated_panel
from ...core.ui_mixins.input_panel import show_single_line_input_panel
from .. import gitlab
from .. import git_mixins
# from ...common import interwebs
from ...common import util
# from ...core.commands.push import GsPushToBranchNameCommand


PUSH_PROMPT = ("You have not set an upstream for the active branch.  "
               "Would you like to push to a remote?")


class GsGitlabMergeRequestCommand(WindowCommand, GitCommand, git_mixins.GitLabRemotesMixin):

    """
    Display open merge requests on the base repo.  When a merge request is selected,
    allow the user to 1) checkout the MR as detached HEAD, 2) checkout the MR as
    a local branch, 3) view the MR's diff, or 4) open the MR in the browser.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.remote_url = self.get_integrated_remote_url()
        self.base_remote = gitlab.parse_remote(self.remote_url)
        self.merge_requests = gitlab.get_merge_requests(self.base_remote)

        pp = show_paginated_panel(
            self.merge_requests,
            self.on_select_mr,
            limit=self.savvy_settings.get("gitlab_per_page_max", 100),
            format_item=self.format_item,
            status_message="Getting merge requests..."
        )
        if pp.is_empty():
            sublime.status_message("No merge requests found.")

    def format_item(self, mr):
        """ Format option items for each merge request """
        return (
            [
                "{number}: {title}".format(number=mr["iid"], title=mr["title"]),
                "Merge request created by {user}, {time_stamp}.".format(
                    user=mr["author"]["username"],
                    time_stamp=util.dates.fuzzy(mr["created_at"],
                                                date_format="%Y-%m-%dT%H:%M:%S.%fZ")
                )
            ],
            mr
        )

    def on_select_mr(self, mr):
        if not mr:
            return

        self.mr = mr
        self.window.show_quick_panel(
            ["Checkout as local branch.",
             "Create local branch, but do not checkout.",
             "View diff.",
             "Open in browser."],
            self.on_select_action
        )

    def on_select_action(self, idx):
        if idx == -1:
            return

        # NOTE: not sure if it's possible to checkout detached without
        #       access to the source repository/branch
        # if idx == 0:
        #     self.fetch_and_checkout_mr()
        elif idx == 0:
            show_single_line_input_panel(
                "Enter branch name for MR {}:".format(self.mr["iid"]),
                "{}/{}".format(self.mr["author"]["username"], self.mr["source_branch"]),
                self.fetch_and_checkout_mr
            )
        elif idx == 1:
            show_single_line_input_panel(
                "Enter branch name for MR {}:".format(self.mr["iid"]),
                "{}/{}".format(self.mr["author"]["username"], self.mr["source_branch"]),
                self.create_branch_for_mr
            )
        elif idx == 2:
            self.view_diff_for_mr()
        elif idx == 3:
            self.open_mr_in_browser()

    def fetch_and_checkout_mr(self, branch_name):
        self.create_branch_for_mr(branch_name)
        sublime.status_message("Checking out MR...")
        self.checkout_ref(branch_name)

    def create_branch_for_mr(self, branch_name):
        sublime.status_message("Fetching MR commit...")
        merge_request_ref = 'merge-requests/{0}/head:{1}'.format(
            self.mr['iid'], branch_name)
        self.git(
            "fetch",
            self.remote_url,
            merge_request_ref
        )

    def view_diff_for_mr(self):
        mr_changes = gitlab.get_merge_request_changes(
            self.base_remote, mr_id=self.mr['iid'])

        diff_view = util.view.get_scratch_view(self, "mr_diff", read_only=True)
        diff_view.set_name("MR #{}".format(self.mr["iid"]))
        diff_view.set_syntax_file("Packages/GitSavvy/syntax/diff.sublime-syntax")

        self.window.focus_view(diff_view)
        diff_text = (change['diff'] for change in mr_changes['changes'])
        diff_view.sel().clear()
        diff_view.run_command("gs_replace_view_text", {
            "text": '\n'.join(diff_text)
        })

    def open_mr_in_browser(self):
        open_in_browser(self.mr["web_url"])


# class GsCreateMergeRequestCommand(WindowCommand, GitCommand, git_mixins.GithubRemotesMixin):
#     """
#     Create merge request of the current commit on the current repo.
#     """

#     def run(self):
#         sublime.set_timeout_async(self.run_async, 0)

#     def run_async(self):
#         if not self.get_upstream_for_active_branch():
#             if sublime.ok_cancel_dialog(PUSH_PROMPT):
#                 self.window.run_command(
#                     "gs_push_and_create_merge_request",
#                     {"set_upstream": True})

#         else:
#             remote_branch = self.get_active_remote_branch()
#             if not remote_branch:
#                 sublime.message_dialog("Unable to determine remote.")
#            else:
#                status, secondary = self.get_branch_status()
#                if secondary:
#                    secondary = "\n".join(secondary)
#                    if "ahead" in secondary or "behind" in secondary:
#                        sublime.message_dialog(
#                            "Your current branch is different from its remote counterpart.\n" +
#                            secondary)
#                        return

#                owner = github.parse_remote(self.get_remotes()[remote_branch.remote]).owner
#                self.open_comparision_in_browser(
#                    owner,
#                    remote_branch.name
#                )

#     def open_comparision_in_browser(self, owner, branch):
#         base_remote = github.parse_remote(self.get_integrated_remote_url())
#         remote_url = base_remote.url
#         base_owner = base_remote.owner
#         base_branch = self.get_integrated_branch_name()
#         url = "{}/compare/{}:{}...{}:{}?expand=1".format(
#             remote_url,
#             base_owner,
#             urllib.parse.quote_plus(base_branch),
#             owner,
#             urllib.parse.quote_plus(branch)
#         )
#         open_in_browser(url)

# class GsPushAndCreateMergeRequestCommand(GsPushToBranchNameCommand):

#     def do_push(self, *args, **kwargs):
#         super().do_push(*args, **kwargs)
#         self.window.run_command("gs_create_merge_request")
