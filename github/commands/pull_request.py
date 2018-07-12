import sublime
from sublime_plugin import WindowCommand
from webbrowser import open as open_in_browser
import urllib

from ...core.git_command import GitCommand
from ...core.ui_mixins.quick_panel import show_paginated_panel
from ...core.ui_mixins.input_panel import show_single_line_input_panel
from .. import github
from .. import git_mixins
from ...common import interwebs
from ...common import util
from ...core.commands.push import GsPushToBranchNameCommand


PUSH_PROMPT = ("You have not set an upstream for the active branch.  "
               "Would you like to push to a remote?")


class GsGithubPullRequestCommand(WindowCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Display open pull requests on the base repo.  When a pull request is selected,
    allow the user to 1) checkout the PR as detached HEAD, 2) checkout the PR as
    a local branch, 3) view the PR's diff, or 4) open the PR in the browser.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        base_remote = github.parse_remote(self.get_integrated_remote_url())
        self.pull_requests = github.get_pull_requests(base_remote)

        pp = show_paginated_panel(
            self.pull_requests,
            self.on_select_pr,
            limit=self.savvy_settings.get("github_per_page_max", 100),
            format_item=self.format_item,
            status_message="Getting pull requests..."
        )
        if pp.is_empty():
            self.window.status_message("No pull requests found.")

    def format_item(self, issue):
        return (
            [
                "{number}: {title}".format(number=issue["number"], title=issue["title"]),
                "Pull request created by {user}, {time_stamp}.".format(
                    user=issue["user"]["login"],
                    time_stamp=util.dates.fuzzy(issue["created_at"],
                                                date_format="%Y-%m-%dT%H:%M:%SZ")
                )
            ],
            issue
        )

    def on_select_pr(self, pr):
        if not pr:
            return

        self.pr = pr
        self.window.show_quick_panel(
            ["Checkout as detached HEAD.",
             "Checkout as local branch.",
             "Create local branch, but do not checkout.",
             "View diff.",
             "Open in browser."],
            self.on_select_action
        )

    def on_select_action(self, idx):
        if idx == -1:
            return

        if idx == 0:
            self.fetch_and_checkout_pr()
        elif idx == 1:
            show_single_line_input_panel(
                "Enter branch name for PR {}:".format(self.pr["number"]),
                "{}/{}".format(self.pr["user"]["login"], self.pr["head"]["ref"]),
                self.fetch_and_checkout_pr
            )
        elif idx == 2:
            show_single_line_input_panel(
                "Enter branch name for PR {}:".format(self.pr["number"]),
                "{}/{}".format(self.pr["user"]["login"], self.pr["head"]["ref"]),
                self.create_branch_for_pr
            )
        elif idx == 3:
            self.view_diff_for_pr()
        elif idx == 4:
            self.open_pr_in_browser()

    def fetch_and_checkout_pr(self, branch_name=None):
        self.window.status_message("Fetching PR commit...")
        self.git(
            "fetch",
            self.pr["head"]["repo"]["clone_url"],
            self.pr["head"]["ref"]
        )

        if branch_name:
            self.window.status_message("Creating local branch for PR...")
            self.git(
                "branch",
                branch_name,
                self.pr["head"]["sha"]
            )

        self.window.status_message("Checking out PR...")
        self.checkout_ref(branch_name or self.pr["head"]["sha"])

    def create_branch_for_pr(self, branch_name):
        self.window.status_message("Fetching PR commit...")
        self.git(
            "fetch",
            self.pr["head"]["repo"]["clone_url"],
            self.pr["head"]["ref"]
        )

        self.window.status_message("Creating local branch for PR...")
        self.git(
            "branch",
            branch_name,
            self.pr["head"]["sha"]
        )

    def view_diff_for_pr(self):
        response = interwebs.get_url(self.pr["diff_url"])

        diff_view = util.view.get_scratch_view(self, "pr_diff", read_only=True)
        diff_view.set_name("PR #{}".format(self.pr["number"]))
        diff_view.set_syntax_file("Packages/GitSavvy/syntax/diff.sublime-syntax")

        self.window.focus_view(diff_view)
        diff_view.sel().clear()
        diff_view.run_command("gs_replace_view_text", {
            "text": response.payload.decode("utf-8")
        })

    def open_pr_in_browser(self):
        open_in_browser(self.pr["html_url"])


class GsGithubCreatePullRequestCommand(WindowCommand, GitCommand, git_mixins.GithubRemotesMixin):
    """
    Create pull request of the current commit on the current repo.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        if not self.get_upstream_for_active_branch():
            if sublime.ok_cancel_dialog(PUSH_PROMPT):
                self.window.run_command(
                    "gs_github_push_and_create_pull_request",
                    {"set_upstream": True})

        else:
            remote_branch = self.get_active_remote_branch()
            if not remote_branch:
                sublime.message_dialog("Unable to determine remote.")
            else:
                status, secondary = self.get_branch_status()
                if secondary:
                    secondary = "\n".join(secondary)
                    if "ahead" in secondary or "behind" in secondary:
                        sublime.message_dialog(
                            "Your current branch is different from its remote counterpart.\n" +
                            secondary)
                        return

                owner = github.parse_remote(self.get_remotes()[remote_branch.remote]).owner
                self.open_comparision_in_browser(
                    owner,
                    remote_branch.name
                )

    def open_comparision_in_browser(self, owner, branch):
        base_remote = github.parse_remote(self.get_integrated_remote_url())
        remote_url = base_remote.url
        base_owner = base_remote.owner
        base_branch = self.get_integrated_branch_name()
        url = "{}/compare/{}:{}...{}:{}?expand=1".format(
            remote_url,
            base_owner,
            urllib.parse.quote_plus(base_branch),
            owner,
            urllib.parse.quote_plus(branch)
        )
        open_in_browser(url)


class GsGithubPushAndCreatePullRequestCommand(GsPushToBranchNameCommand):

    def do_push(self, *args, **kwargs):
        super().do_push(*args, **kwargs)
        self.window.run_command("gs_github_create_pull_request")
