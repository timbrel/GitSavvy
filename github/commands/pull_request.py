import sublime
from webbrowser import open as open_in_browser
import urllib

from .. import github
from .. import git_mixins
from ...common import interwebs
from ...common import util
from ...core.commands.push import gs_push_to_branch_name
from ...core.fns import filter_
from ...core.ui_mixins.quick_panel import show_paginated_panel
from ...core.ui_mixins.input_panel import show_single_line_input_panel
from ...core.view import replace_view_content
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.runtime import on_worker, run_as_future


__all__ = (
    "gs_github_pull_request",
    "gs_github_create_pull_request",
    "gs_github_push_and_create_pull_request",
)


from GitSavvy.core.git_mixins.branches import Upstream


class gs_github_pull_request(GsWindowCommand, git_mixins.GithubRemotesMixin):

    """
    Display pull requests on the base repo.  When a pull request is selected,
    allow the user to 1) checkout the PR as detached HEAD, 2) checkout the PR as
    a local branch, 3) view the PR's diff, or 4) open the PR in the browser.

    By default, all "open" pull requests are displayed.  This can be customized
    using the `query` arg which is of the same query format as in the Web UI of
    Github.  Note that "repo:", "type:", and "state:" are prefilled if omitted.
    """

    @on_worker
    def run(self, query=""):
        self.remotes = self.get_remotes()
        self.base_remote_name = self.get_integrated_remote_name(self.remotes)
        self.base_remote_url = self.remotes[self.base_remote_name]
        self.base_remote = repository = github.parse_remote(self.base_remote_url)

        query_ = " ".join(filter_((
            f"repo:{repository.owner}/{repository.repo}" if "repo:" not in query else None,
            "type:pr" if "type:" not in query else None,
            "state:open" if "state:" not in query else None,
            query.strip()
        )))
        self.pull_requests = github.search_pull_requests(self.base_remote, query_)

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

        self.pr_ = run_as_future(github.get_pull_request, pr["number"], self.base_remote)
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

        # Note that the request starts in `on_select_pr`.  So the actual wait time includes the
        # time we wait for the user to take action.
        timeout = 4.0
        try:
            self.pr = self.pr_.result(timeout)
        except TimeoutError:
            self.window.status_message(f"Timeout: could not fetch the PR details within {timeout} seconds.")
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
        self.window.run_command("gs_checkout_branch", {"branch": "FETCH_HEAD"})

    def create_branch_for_pr(self, branch_name, checkout=False, ask_set_upstream=True):
        if not branch_name:
            return

        url = self.best_remote_url_for_pr()
        ref = self.pr["head"]["ref"]

        owner = self.pr["head"]["repo"]["owner"]["login"]
        if owner == self.base_remote.owner:
            owner = self.base_remote_name
            ask_set_upstream = False
        elif owner in self.remotes.keys():
            ask_set_upstream = False

        remote_ref = "{}/{}".format(owner, ref)
        if ask_set_upstream:
            answer = sublime.yes_no_cancel_dialog("Set upstream to '{}'?".format(remote_ref))
            if answer == sublime.DIALOG_CANCEL:
                return
            set_upstream = answer == sublime.DIALOG_YES
        else:
            set_upstream = True

        self.window.status_message("Creating local branch for PR...")
        if set_upstream:
            if owner not in self.remotes.keys():
                self.git("remote", "add", owner, url)
                self.git("config", f"remote.{owner}.push", "+refs/heads/*:refs/heads/*")
                self.git("config", f"remote.{owner}.tagOpt", "--no-tags")
            self.git("fetch", owner, ref)
            self.update_store({"last_remote_used": owner})
            self.git("branch", branch_name, "FETCH_HEAD")
            self.git("branch", "-u", remote_ref, branch_name)
        else:
            self.git("fetch", url, ref)
            self.git("branch", branch_name, "FETCH_HEAD")

        if checkout:
            self.window.run_command("gs_checkout_branch", {"branch": branch_name})
        else:
            util.view.refresh_gitsavvy_interfaces(self.window)

    def best_remote_url_for_pr(self):
        clone_url = self.pr["head"]["repo"]["clone_url"]
        ssh_url = self.pr["head"]["repo"]["ssh_url"]
        return ssh_url if self.base_remote_url.startswith("git@") else clone_url

    def view_diff_for_pr(self):
        response = interwebs.get_url(self.pr["diff_url"])

        diff_view = util.view.create_scratch_view(self.window, "pr_diff", {
            "title": "PR #{}".format(self.pr["number"]),
            "syntax": "Packages/GitSavvy/syntax/diff.sublime-syntax",
        })
        replace_view_content(diff_view, response.payload.decode("utf-8"))

    def open_pr_in_browser(self):
        open_in_browser(self.pr["html_url"])


class gs_github_create_pull_request(GsWindowCommand, git_mixins.GithubRemotesMixin):
    """
    Create pull request of the current commit on the current repo.
    """

    @on_worker
    def run(self):
        current_branch = self.get_current_branch()
        if not current_branch:
            sublime.message_dialog("You're on a detached HEAD.  Can't push in that state.")
            return

        if not current_branch.upstream:
            self.window.run_command("gs_github_push_and_create_pull_request", {
                "local_branch_name": current_branch.name,
                "set_upstream": True
            })

        elif (
            "ahead" in current_branch.upstream.status
            or "behind" in current_branch.upstream.status
        ):
            sublime.message_dialog(
                "Your current branch is different from '{}'.\n{}".format(
                    current_branch.upstream.canonical_name, current_branch.upstream.status
                )
            )

        else:
            self.open_comparison_in_browser(current_branch.upstream)

    def open_comparison_in_browser(self, upstream):
        # type: (Upstream) -> None
        remotes = self.get_remotes()

        remote_url = remotes[upstream.remote]
        owner = github.parse_remote(remote_url).owner

        config = self.read_gitsavvy_config()
        base_remote_name = self.get_integrated_remote_name(
            remotes,
            current_upstream=upstream,
            configured_remote_name=config.get("ghremote")
        )
        base_remote_url = remotes[base_remote_name]
        base_remote = github.parse_remote(base_remote_url)
        base_branch = config.get("ghbranch")

        start = (
            "{}:{}...".format(base_remote.owner, urllib.parse.quote_plus(base_branch))
            if base_branch
            else ""
        )
        end = "{}:{}".format(owner, urllib.parse.quote_plus(upstream.branch))
        url = "{}/compare/{}{}?expand=1".format(base_remote.url, start, end)
        open_in_browser(url)


class gs_github_push_and_create_pull_request(gs_push_to_branch_name):

    def do_push(self, *args, **kwargs):
        super().do_push(*args, **kwargs)
        self.window.run_command("gs_github_create_pull_request")
