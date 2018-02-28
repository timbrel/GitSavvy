# GitLab integration

GitSavvy provides some basic integration with GitLab.  More features are planned for future versions.  At present, both GitLab.com and GitLab enterprise are supported.


## Setup

When interacting with a public repository, no configuration is required.  However, if your repository is private, you will need to add an API key to the GitSavvy configuration.  To do so:

1. [Create a personal access token.](https://docs.gitlab.com/ee/user/profile/personal_access_tokens.html#creating-a-personal-access-token); `api` scope should be checked.
2. After submitting, copy the generated API key.
3. In the Sublime Menu, open `Preferences > Package Settings > GitSavvy > Settings - User`.
4. Add your key to the `api_tokens` object (you can find an example in `Preferences > Package Settings > GitSavvy > Settings`).


## Choosing a remote

By default, GitSavvy will use the URL of `origin` to get its GitLab API URL.  If you would like to use a remote other than origin, run `gitlab: set remote for integration` in the command palette.  You will be presented with a list of remotes.  Once one is selected, this remote will be used for all attempts at integration with GitLab.

## `gitlab: review pull request`

This command will display all open pull requests for the integrated GitLab remote.  Once you have made a selection, you can either checkout the pull request as a detached HEAD, checkout the pull request as a local branch, create a branch but not check it out, view the diff of the pull request, or open the pull request in the browser.
