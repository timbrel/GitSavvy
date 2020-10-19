import sublime
from sublime_plugin import WindowCommand
from webbrowser import open as open_in_browser
import urllib

from .. import github
from .. import git_mixins
from ...common import interwebs
from ...common import util
from ...core.commands.push import gs_push_to_branch_name
from ...core.git_command import GitCommand
from ...core.ui_mixins.quick_panel import show_paginated_panel
from ...core.ui_mixins.input_panel import show_single_line_input_panel
from ...core.view import replace_view_content


PUSH_PROMPT = ("You have not set an upstream for the active branch.  "
               "Would you like to push to a remote?")


class GsGithubPullRequestCommand(WindowCommand, git_mixins.GithubRemotesMixin, GitCommand):

    """
    Display open pull requests on the base repo.  When a pull request is selected,
    allow the user to 1) checkout the PR as detached HEAD, 2) checkout the PR as
    a local branch, 3) view the PR's diff, or 4) open the PR in the browser.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.remotes = self.get_remotes()
        self.base_remote_name = self.get_integrated_remote_name(self.remotes)
        self.base_remote_url = self.remotes[self.base_remote_name]
        self.base_remote = github.parse_remote(self.base_remote_url)
        self.pull_requests = github.get_pull_requests(self.base_remote)

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
             "Create local branch without checking out.",
             "View diff.",
             "Open in browser."],
            self.on_select_action
        )

    def on_select_action(self, idx):
        if idx == -1:
            return

        owner = self.pr["head"]["repo"]["owner"]["login"]

        if owner == self.base_remote.owner:
            # don't add prefix for integrated remote
            branch_name = self.pr["head"]["ref"]
        else:
            branch_name = "{}-{}".format(owner, self.pr["head"]["ref"])

        if idx == 0:
            self.checkout_detached()
        elif idx == 1:
            show_single_line_input_panel(
                "Enter branch name for PR {}:".format(self.pr["number"]),
                branch_name,
                lambda branch_name: self.create_branch_for_pr(branch_name, checkout=True)
            )
        elif idx == 2:
            show_single_line_input_panel(
                "Enter branch name for PR {}:".format(self.pr["number"]),
                branch_name,
                lambda branch_name: self.create_branch_for_pr(branch_name, checkout=False)
            )
        elif idx == 3:
            self.view_diff_for_pr()
        elif idx == 4:
            self.open_pr_in_browser()

    def checkout_detached(self):
        self.window.status_message("Checking out PR...")

        url = self.best_remote_url_for_pr()
        ref = self.pr["head"]["ref"]
        self.git("fetch", url, ref)
        self.checkout_ref("FETCH_HEAD")
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def create_branch_for_pr(self, branch_name, checkout=False, ask_set_upstream=True):
        if not branch_name:
            return

        url = self.best_remote_url_for_pr()
        ref = self.pr["head"]["ref"]

        owner = self.pr["head"]["repo"]["owner"]["login"]
        if owner == self.base_remote.owner:
            owner = self.base_remote_name
            ask_set_upstream = False

        remote_ref = "{}/{}".format(owner, ref)
        set_upstream = sublime.ok_cancel_dialog(
            "Set upstream to '{}'?".format(remote_ref)) if ask_set_upstream else True

        self.window.status_message("Creating local branch for PR...")
        if set_upstream:
            if owner not in self.remotes.keys():
                self.git("remote", "add", owner, url)
            self.git("fetch", owner, ref)
            self.git("branch", branch_name, "FETCH_HEAD")
            self.git("branch", "-u", remote_ref, branch_name)
        else:
            self.git("fetch", url, ref)
            self.git("branch", branch_name, "FETCH_HEAD")

        if checkout:
            self.checkout_ref(branch_name)

        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def best_remote_url_for_pr(self):
        clone_url = self.pr["head"]["repo"]["clone_url"]
        ssh_url = self.pr["head"]["repo"]["ssh_url"]
        return ssh_url if self.base_remote_url.startswith("git@") else clone_url

    def view_diff_for_pr(self):
        response = interwebs.get_url(self.pr["diff_url"])

        diff_view = util.view.get_scratch_view(self, "pr_diff", read_only=True)
        diff_view.set_name("PR #{}".format(self.pr["number"]))
        diff_view.set_syntax_file("Packages/GitSavvy/syntax/diff.sublime-syntax")
        replace_view_content(diff_view, response.payload.decode("utf-8"))

    def open_pr_in_browser(self):
        open_in_browser(self.pr["html_url"])


class GsGithubCreatePullRequestCommand(WindowCommand, git_mixins.GithubRemotesMixin, GitCommand):
    """
    Create pull request of the current commit on the current repo.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        current_branch = self.get_current_branch()
        if not current_branch:
            sublime.message_dialog("You're on a detached HEAD.  Can't push in that state.")
            return

        if not current_branch.tracking:
            if sublime.ok_cancel_dialog(PUSH_PROMPT):
                self.window.run_command(
                    "gs_github_push_and_create_pull_request",
                    {"set_upstream": True})

        else:
            remote, remote_branch = current_branch.tracking.split("/", 1)
            if (
                "ahead" in current_branch.tracking_status
                or "behind" in current_branch.tracking_status
            ):
                sublime.message_dialog(
                    "Your current branch is different from '{}'.\n{}".format(
                        current_branch.tracking, current_branch.tracking_status
                    )
                )
                return

            remote_url = self.get_remotes()[remote]
            owner = github.parse_remote(remote_url).owner
            self.open_comparision_in_browser(
                owner,
                remote_branch
            )

    def open_comparision_in_browser(self, owner, branch):
        base_remote = github.parse_remote(self.get_integrated_remote_url())
        remote_url = base_remote.url
        base_owner = base_remote.owner
        base_branch = self.get_integrated_branch_name()
        start = (
            "{}:{}...".format(base_owner, urllib.parse.quote_plus(base_branch))
            if base_branch
            else ""
        )
        end = "{}:{}".format(owner, urllib.parse.quote_plus(branch))
        url = "{}/compare/{}{}?expand=1".format(remote_url, start, end)
        open_in_browser(url)


class GsGithubPushAndCreatePullRequestCommand(gs_push_to_branch_name):

    def do_push(self, *args, **kwargs):
        super().do_push(*args, **kwargs)
        self.window.run_command("gs_github_create_pull_request")
