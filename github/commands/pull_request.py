import sublime
from sublime_plugin import TextCommand
from webbrowser import open as open_in_browser

from ...core.git_command import GitCommand
from .. import github
from .. import git_mixins
from ...common import interwebs
from ...common import util


def create_palette_entry(pr):
    return [
        "{number} {title}".format(number=pr["number"], title=pr["title"]),
        "created by {user} at {time_stamp}".format(
            user=pr["head"]["user"]["login"],
            time_stamp=pr["created_at"]
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
            self.view.window.show_input_panel(
                "Enter branch name for PR {}:".format(self.pr["number"]),
                "pull-request-{}".format(self.pr["number"]),
                self.fetch_and_checkout_pr
                )
        elif idx == 2:
            self.view_diff_for_pr()
        elif idx == 3:
            self.open_pr_in_browser()

    def fetch_and_checkout_pr(self, branch_name=None):
        sublime.status_message("Fetching PR commit...")
        self.git(
            "fetch",
            self.pr["head"]["repo"]["clone_url"],
            self.pr["head"]["sha"]
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

    def view_diff_for_pr(self):
        response = interwebs.get_url(self.pr["diff_url"])
        print("getting", self.pr["diff_url"])
        print(repr(response.payload.decode("utf-8")))

        diff_view = util.view.get_read_only_view(self, "pr_diff")
        diff_view.set_name("PR #{}".format(self.pr["number"]))
        diff_view.set_syntax_file("Packages/Diff/Diff.tmLanguage")

        self.view.window().focus_view(diff_view)
        diff_view.sel().clear()
        diff_view.run_command("gs_replace_view_text", {
            "text": response.payload.decode("utf-8")
            })

    def open_pr_in_browser(self):
        open_in_browser(self.pr["html_url"])
