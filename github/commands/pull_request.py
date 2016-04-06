import sublime, datetime
from sublime_plugin import TextCommand
from webbrowser import open as open_in_browser

from ...core.git_command import GitCommand
from .. import github
from .. import git_mixins
from ...common import interwebs
from ...common import util

SET_UPSTREAM_PROMPT = ("You have not set an upstream for the active branch.  "
                       "Would you like to set one?")


def create_palette_entry(pr):
    return [
        "{number}: {title}".format(number=pr["number"], title=pr["title"]),
        "Created by {user}, {time_stamp}.".format(
            user=pr["user"]["login"],
            time_stamp=util.dates.fuzzy(pr["created_at"], date_format="%Y-%m-%dT%H:%M:%SZ")
            )
    ]


class GsPullRequestCommand(TextCommand, GitCommand, git_mixins.GithubRemotesMixin):

    """
    Display open pull requests on the base repo.  When a pull request is selected,
    allow the user to 1) checkout the PR as detached HEAD, 2) checkout the PR as
    a local branch, 3) view the PR's diff, or 4) open the PR in the browser.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        base_remote = github.parse_remote(self.get_integrated_remote_url())
        self.pull_requests = github.get_pull_requests(base_remote)

        self.view.window().show_quick_panel(
            [create_palette_entry(pr) for pr in self.pull_requests],
            self.on_select_pr
            )

    def on_select_pr(self, idx):
        if idx == -1:
            return

        self.pr = self.pull_requests[idx]

        self.view.window().show_quick_panel(
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
            self.view.window().show_input_panel(
                "Enter branch name for PR {}:".format(self.pr["number"]),
                "pull-request-{}".format(self.pr["number"]),
                self.fetch_and_checkout_pr,
                None,
                None
                )
        elif idx == 2:
            self.view.window().show_input_panel(
                "Enter branch name for PR {}:".format(self.pr["number"]),
                "pull-request-{}".format(self.pr["number"]),
                self.create_branch_for_pr,
                None,
                None
                )
        elif idx == 3:
            self.view_diff_for_pr()
        elif idx == 4:
            self.open_pr_in_browser()

    def fetch_and_checkout_pr(self, branch_name=None):
        sublime.status_message("Fetching PR commit...")
        self.git(
            "fetch",
            self.pr["head"]["repo"]["clone_url"],
            self.pr["head"]["ref"]
            )

        if branch_name:
            sublime.status_message("Creating local branch for PR...")
            self.git(
                "branch",
                branch_name,
                self.pr["head"]["sha"]
                )

        sublime.status_message("Checking out PR...")
        self.checkout_ref(branch_name or self.pr["head"]["sha"])

    def create_branch_for_pr(self, branch_name):
        sublime.status_message("Fetching PR commit...")
        self.git(
            "fetch",
            self.pr["head"]["repo"]["clone_url"],
            self.pr["head"]["ref"]
            )

        sublime.status_message("Creating local branch for PR...")
        self.git(
            "branch",
            branch_name,
            self.pr["head"]["sha"]
            )

    def view_diff_for_pr(self):
        response = interwebs.get_url(self.pr["diff_url"])
        print("getting", self.pr["diff_url"])
        print(repr(response.payload.decode("utf-8")))

        diff_view = util.view.get_scratch_view(self, "pr_diff", read_only=True)
        diff_view.set_name("PR #{}".format(self.pr["number"]))
        diff_view.set_syntax_file("Packages/GitSavvy/syntax/diff.sublime-syntax")

        self.view.window().focus_view(diff_view)
        diff_view.sel().clear()
        diff_view.run_command("gs_replace_view_text", {
            "text": response.payload.decode("utf-8")
            })

    def open_pr_in_browser(self):
        open_in_browser(self.pr["html_url"])


class GsCreatePullRequestCommand(TextCommand, GitCommand, git_mixins.GithubRemotesMixin):
    """
    Create pull request of the current commit on the current repo.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        if savvy_settings.get("prompt_for_tracking_branch") and not self.get_upstream_for_active_branch():
            if sublime.ok_cancel_dialog(SET_UPSTREAM_PROMPT):
                self.remotes = list(self.get_remotes().keys())

                if not self.remotes:
                    self.view.window().show_quick_panel(["There are no remotes available."], None)
                else:
                    self.view.window().show_quick_panel(
                        self.remotes,
                        self.on_select_remote,
                        flags=sublime.MONOSPACE_FONT
                        )

        else:
            remote_branch = self.get_active_remote_branch()
            if not remote_branch:
                sublime.message_dialog("Unable to determine remote.")
            else:
                status, secondary = self.get_branch_status()
                if secondary:
                    sublime.message_dialog(
                        "Your current branch is different from its remote counterpart. %s" % secondary)
                else:
                    base_remote = github.parse_remote(self.get_integrated_remote_url())
                    owner = github.parse_remote(self.get_remotes()[remote_branch.remote]).owner
                    self.open_comparision_in_browser(base_remote.url, owner, remote_branch.name)

    def open_comparision_in_browser(self, url, owner, branch):
        open_in_browser("{}/compare/{}:{}?expand=1".format(
            url,
            owner,
            branch
        ))

    def on_select_remote(self, remote_index):
        """
        After the user selects a remote, display a panel of branches that are
        present on that remote, then proceed to `on_select_branch`.
        """
        # If the user pressed `esc` or otherwise cancelled.
        if remote_index == -1:
            return

        self.selected_remote = self.remotes[remote_index]
        self.branches_on_selected_remote = self.list_remote_branches(self.selected_remote)

        self.current_local_branch = self.get_current_branch_name()

        try:
            pre_selected_index = self.branches_on_selected_remote.index(
                self.selected_remote + "/" + self.current_local_branch)
        except ValueError:
            pre_selected_index = 0

        self.view.window().show_quick_panel(
            self.branches_on_selected_remote,
            self.on_select_branch,
            flags=sublime.MONOSPACE_FONT,
            selected_index=pre_selected_index
        )

    def on_select_branch(self, branch_index):
        """
        Determine the actual branch name of the user's selection, and proceed
        to `do_pull`.
        """
        # If the user pressed `esc` or otherwise cancelled.
        if branch_index == -1:
            return
        selected_remote_branch = self.branches_on_selected_remote[branch_index].split("/", 1)[1]
        remote_ref = self.selected_remote + "/" + selected_remote_branch

        self.current_local_branch = self.get_current_branch_name()

        self.git("branch", "-u", remote_ref, self.current_local_branch)

        sublime.set_timeout_async(self.run_async, 0)
