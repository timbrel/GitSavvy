# GitHub integration

GitSavvy provides some basic integration with GitHub.  More features are planned for future versions.  At present, both GitHub.com and GitHub enterprise are supported.


## Setup

When interacting with a public repository, no configuration is required.  However, if your repository is private, you will need to add an API key to the GitSavvy configuration.  To do so:

1. [Create a new API token.](https://github.com/settings/tokens/new).  `repo` and `public_repo` should be checked.
2. After submitting, copy the generated API key.
3. In the Sublime Menu, open `Preferences > Package Settings > GitSavvy > Settings - User`.
4. Add your key to the `api_tokens` object (you can find an example in `Preferences > Package Settings > GitSavvy > Settings`).


## Choosing a remote

By default, GitSavvy will use the URL of `origin` to get its GitHub API URL.  If you would like to use a remote other than origin, run `github: set remote for integration` in the command palette.  You will be presented with a list of remotes.  Once one is selected, this remote will be used for all attempts at integration with GitHub.


## `github: open file on remote`

This command is accessible via both the command palette and the status dashboard.  GitSavvy will attempt to determine the GitHub web URL for the file at the current hash, and open a browser with that URL.

When run from within a file (not the status dashboard), any selected lines will also be preselected on GitHub in the browser.


## `github: open issues`

This command will open a new browser window, displaying the issues page for the integrated GitHub remote.


## `github: open repo`

This command will open a new browser window, displaying the landing page for the integrated GitHub remote.

## `github: review pull request`

This command will display all open pull requests for the integrated GitHub remote.  Once you have made a selection, you can either checkout the pull request as a detached HEAD, checkout the pull request as a local branch, create a branch but not check it out, view the diff of the pull request, or open the pull request in the browser.


## `github: create pull request`

This command will create a pull request in the browser on the integrated GitHub remote repo.

## `github: create fork`

This command will create a fork of the current GitHub remote repo in the configured user.

## `github: add fork as remote`

Add a new remote from a list of repos on GitHub forking from the active repo.


## `issues integration in commit view`

When writing a commit message, you can easily reference GitHub issues.  Type `#` followed by pressing the `Tab` key.  A pop-up will be shown with a list of the open issues for the repo.  If you'd like to reference issues from a separate repository, you can do so by typing `owner/repo#` and pressing `Tab`.  For example, to reference a GitSavvy issue you might type `divmain/GitSavvy#`, press `Tab`, and a list of GitSavvy issues will be displayed in the pop-up.

These are nice shortcuts to use with GitHub's `Closes #x` functionality, where issue `x` will be closed when the commit is merged into the `master` branch.

## `contributors integration in commit view`

If you'd like to reference or ping contributors to the repo you're working on, type `@` and then press `Tab`.  A list of contributor usernames will pop-up for your reference.
