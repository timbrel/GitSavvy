import sublime
from webbrowser import open as open_in_browser

from .. import gitlab
from .. import git_mixins
from ...core.ui_mixins.quick_panel import show_paginated_panel
from ...core.ui_mixins.input_panel import show_single_line_input_panel
from ...core.view import replace_view_content
from ...common import util
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.runtime import on_worker
from GitSavvy.core.ui__quick_panel import show_quick_panel


__all__ = (
    "gs_gitlab_merge_request",
)


PUSH_PROMPT = ("You have not set an upstream for the active branch.  "
               "Would you like to push to a remote?")


class gs_gitlab_merge_request(GsWindowCommand, git_mixins.GitLabRemotesMixin):

    """
    Display open merge requests on the base repo.  When a merge request is selected,
    allow the user to 1) checkout the MR as detached HEAD, 2) checkout the MR as
    a local branch, 3) view the MR's diff, or 4) open the MR in the browser.
    """

    @on_worker
    def run(self):
        self.remote_url = self.get_integrated_remote_url()
        self.base_remote = gitlab.parse_remote(self.remote_url)
        self.merge_requests = gitlab.get_merge_requests(self.base_remote, {}, {'state': "opened"})

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
        show_quick_panel(
            self.window,
            [
                "Checkout as local branch.",
                "Create local branch, but do not checkout.",
                "View diff.",
                "Open in browser.",
            ],
            self.on_select_action
        )

    def on_select_action(self, idx):
        # NOTE: not sure if it's possible to checkout detached without
        #       access to the source repository/branch
        # if idx == 0:
        #     self.fetch_and_checkout_mr()
        if idx == 0:
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
            self.base_remote, {'mr_id': self.mr['iid']})

        diff_view = util.view.create_scratch_view(self.window, "mr_diff", {
            "title": "MR #{}".format(self.mr["iid"]),
            "syntax": "Packages/GitSavvy/syntax/diff.sublime-syntax",
        })

        diff_text = '\n'.join(change['diff'] for change in mr_changes['changes'])
        replace_view_content(diff_view, diff_text)

    def open_mr_in_browser(self):
        open_in_browser(self.mr["web_url"])
