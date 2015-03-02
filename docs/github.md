# GitHub integration

GitSavvy provides some basic integration with GitHub.  More features are planned for future versions.  At present, both GitHub.com and GitHub enterprise are supported.


## `github: open file on remote`

This command is accessible via both the command palette and the status dashboard.  GitSavvy will attempt to determine the GitHub web URL for the file at the current hash, and open a browser with that URL.

When run from within a file (not the status dashboard), any selected lines will also be preselected on GitHub in the browser.


## `issues integration in commit view`

When writing a commit message, you can easily reference GitHub issues.  Type `#` followed by pressing the `Tab` key.  A pop-up will be shown with a list of the open issues for the repo.  If you'd like to reference issues from a separate repository, you can do so by typing `owner/repo#` and pressing `Tab`.  For example, to reference a GitSavvy issue you might type `divmain/GitSavvy#`, press `Tab`, and a list of GitSavvy issues will be displayed in the pop-up.

These are nice shortcuts to use with GitHub's `Closes #x` functionality, where issue `x` will be closed when the commit is merged into the `master` branch.

## `contributors integration in commit view`

If you'd like to reference or ping contributors to the repo you're working on, type `@` and then press `Tab`.  A list of contributor usernames will pop-up for your reference.
