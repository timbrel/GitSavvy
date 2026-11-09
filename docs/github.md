# GitHub integration

GitSavvy provides some basic integration with GitHub.  More features are planned for future versions.  At present, both GitHub.com and GitHub enterprise are supported.


## Setup

When interacting with a public repository, no configuration is required.  However, if your repository is private, you will need to add an API key to the GitSavvy configuration.  To do so:

1. [Create a new API token.](https://github.com/settings/tokens/new).  `repo` and `public_repo` should be checked.
2. After submitting, copy the generated API key.
3. In the Sublime Menu, open `Preferences > Package Settings > GitSavvy > Settings - User`.
4. Add your key to the `api_tokens` object (you can find an example in `Preferences > Package Settings > GitSavvy > Settings`).
5. You can alternatively set the `GITHUB_TOKEN` environment variable. GitSavvy uses it as a fallback when no token is configured for `github.com` in `api_tokens`. GitHub Enterprise instances *require* a host-specific entry in `api_tokens`.


## CI status after pushing

After a successful push with GitSavvy, the status bar shows GitHub's check status of the pushed commit, the same aggregate GitHub shows next to a commit, including external CI.  While checks are running you see an animated `[◇]`; until GitHub reports any checks for the commit, nothing is shown, so a repository without CI stays quiet.  A success is acknowledged with `[CI: ok]` for a few seconds and then disappears; a failure shows `[CI: failed]` and then stays as `[red]` for that branch and commit.

GitSavvy also looks up the checks once whenever it sees that you moved to another commit of a branch which has an upstream and is not ahead of it, or checked out a commit directly (detached HEAD), e.g. after switching branches, after pulling, or when you open a repository.  These lookups are silent: there is no animation, and only a failure shows as `[red]`.  Nothing is looked up for unpushed commits on a branch, or during a rebase, merge, cherry-pick, revert, or bisect.

The branch dashboard shows the checks of every pushed local branch next to its upstream: `[ok]`, `[red]`, or `[pending]`, and nothing if a commit has no checks.  Every refresh of the dashboard looks them up again, with a single request for up to 50 branches, the most recently committed first.  A branch is considered pushed if it has an upstream which is not gone and it is not ahead of it.  These results also update the status bar.

The checks are looked up in the integrated remote (see below), so pushing to your fork reports the checks of the upstream repository for your commit.  Monitoring requires an API token as described above, and is skipped for non-GitHub hosts, mirrors, and pushes to multiple destinations.  GitHub Enterprise hosts must be listed in the `github_enterprise_hosts` setting.

Set `github_ci_status_in_status_bar` to `false` to turn this off, in the status bar and in the branch dashboard.  It is independent of `git_status_in_status_bar`.


## Choosing a remote

By default, GitSavvy will use the URL of `origin` to get its GitHub API URL.  If you would like to use a remote other than origin, run `github: set remote for integration` in the command palette.  You will be presented with a list of remotes.  Once one is selected, this remote will be used for all attempts at integration with GitHub.


## `github: create repo` / `github: create private repo`

These command palette commands create a public or private repository on GitHub.com under the authenticated user's account.  Both require an API token configured as described in Setup above, with permission to create repositories.

Enter the new repository's name, then choose a local remote name (defaulting to `origin`) and its HTTPS or SSH URL.  GitSavvy adds the remote to your local repository; it does not push any commits automatically.

## `github: create fork`

This command will create a fork of the current GitHub remote repo in the configured user.

## `github: add fork as remote`

Add a new remote from a list of repos on GitHub forking from the active repo.


## `open commit in the browser`

When looking at a commit, `[h]` will appear in the top right corner when that commit is also available on the upstream.

## `github: open file in browser`

This command is accessible via the command palette and `[h]` in the status dashboard.  It opens the file on the current branch's upstream if that upstream belongs to the selected remote; otherwise it uses the remote's default branch.  Local edits and unpushed commits do not prevent opening the published file.  GitSavvy uses locally known remote-tracking refs, so fetch first if they are out of date.

When opening with line selections (`preselect`), GitSavvy maps the selected lines to the published file, including shifts caused by local edits.  If the file or selected lines are not present there, it reports that instead of opening an unrelated location.  The URL uses the branch name; press `y` on GitHub to turn it into a permalink before sharing.

In a historical file view, `[h]` opens that exact revision and preserves the selected lines.  If the revision has not been pushed to the selected remote, GitSavvy reports it as unpublished rather than substituting another revision.

## `github: open repo`

This command will open a new browser window, displaying the landing page for the integrated GitHub remote.

## `github: open issues`

This command will open a new browser window, displaying the issues page for the integrated GitHub remote.



## `github: create pull request`

This command will create a pull request in the browser on the integrated GitHub remote repo.

## `github: review pull request`

This command will display all open pull requests for the integrated GitHub remote.  Once you have made a selection, you can either checkout the pull request as a detached HEAD, checkout the pull request as a local branch, create a branch but not check it out, view the diff of the pull request, or open the pull request in the browser.



## `issues integration in commit view`

When writing a commit message, you can easily reference GitHub issues.  Type `#` followed by pressing the `Tab` key.  A pop-up will be shown with a list of the open issues for the repo.  If you'd like to reference issues from a separate repository, you can do so by typing `owner/repo#` and pressing `Tab`.  For example, to reference a GitSavvy issue you might type `divmain/GitSavvy#`, press `Tab`, and a list of GitSavvy issues will be displayed in the pop-up.

These are nice shortcuts to use with GitHub's `Closes #x` functionality, where issue `x` will be closed when the commit is merged into the `master` branch.

## `contributors integration in commit view`

If you'd like to reference or ping contributors to the repo you're working on, type `@` and then press `Tab`.  A list of contributor usernames will pop-up for your reference.
