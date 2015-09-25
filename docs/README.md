# GitSavvy Documentation

You've found GitSavvy's online documentation.

All documentation is in markdown.  Beyond what you might expect, if you see a link to a different page, you can browse to that page by pressing **SUPER-Enter** while the link is under your cursor (**CTRL-Enter** on Windows).

To return to the previous page, press **SUPER-Backspace** (**CTRL-Backspace** on Windows).

If you run into any issues not addressed here, please feel free to [open an issue](https://github.com/divmain/GitSavvy/issues) and ask for help!


## Highlighted Features

- [Inline-diff](staging.md#git-diff-current-file-inline)
- [Status view](status.md)
- [GitHub integration](github.md)
- [git-flow support](flow.md)


## Command Palette

### High-level commands

- [git: status](status.md)
- [git: init](misc.md#git-init)


### (Un)Staging changes
- [git: diff current file inline](staging.md#git-diff-current-file-inline)
- [git: diff current file inline (cached)](staging.md#git-diff-current-file-inline-cached)
- [git: quick stage](staging.md#git-quick-stage)
- [git: diff](staging.md#git-diff)
- [git: diff cached](staging.md#git-diff-cached)


### Commiting

- [git: commit](commit.md#git-commit)
- [git: commit including unstaged files](commit.md#git-commit-including-unstaged-files)
- [git: quick commit](commit.md#git-quick-commit)
- [git: quick stage current file and commit](commit.md#git-quick-stage-current-file-and-commit)


### Branch management

- [git: checkout](branch_mgmt.md#git-checkout)
- [git: checkout new branch](branch_mgmt.md#git-checkout-new-branch)
- [git: checkout current file](branch_mgmt.md#git-checkout-current-file)
- [git: merge](branch_mgmt.md#git-merge)
- [git: abort merge](branch_mgmt.md#git-abort-merge)
- [git: restart merge for file...](branch_mgmt.md#git-restart-merge-for-file)


### Tag management

- [git: tags](tag_mgmt.md#git-tags)
- [git: quick tag](tag_mgmt.md#git-quick-tag)


### Modifying history

- [git: rebase](rebase.md)
- [git: amend previous commit](commit.md#git-amend-previous-commit)


### Interacting with remotes

- [git: checkout remote branch as local](remotes.md#git-checkout-remote-branch-as-local)
- [git: fetch](remotes.md#git-fetch)
- [git: pull](remotes.md#git-pull)
- [git: push](remotes.md#git-push)
- [git: push to branch](remotes.md#git-push-to-branch)
- [git: push to branch name](remotes.md#git-push-to-branch-name)


### History

- [git: log](history.md#git-log)
- [git: log current file](history.md#git-log-current-file)
- [git: log by author](history.md#git-log-by-author)
- [git: blame current file](history.md#git-blame-current-file)
- [git: graph](history.md#git-graph)


### Ignoring files

- [git: ignore current file](ignoring.md#git-ignore-current-file)
- [git: ignore pattern](ignoring.md#git-ignore-pattern)
- [git: assume file unchanged](ignoring.md#git-assume-file-unchanged)
- [git: restore file assumed unchanged](ignoring.md#git-restore-file-assumed-unchanged)


### Debug

- [GitSavvy: reload modules (debug)](debug.md#gitsavvy-reload-modules-debug)
- [GitSavvy: start logging](debug.md#gitsavvy-start-logging)
- [GitSavvy: stop logging](debug.md#gitsavvy-stop-logging)
- [GitSavvy: view recorded log](debug.md#gitsavvy-view-recorded-log)


## Special Views

- [Status view](status.md#overview)
- [Inline-diff view](staging.md#git-diff-current-file-inline)
- [Diff view](staging.md#git-diff)
- [Commit view](commit.md)
- [Blame view](history.md#git-blame-current-file)
- [Log-graph view](history.md#git-graph)


## GitHub Integration

- [github: open file on remote](github.md#github-open-file-on-remote)
- [issues integration in commit view](github.md#issues-integration)
- [contributors integration in commit view](github.md#contributors-integration)


## Custom Commands

If you have the need, you can add your own commands that take advantage of GitSavvy's access to your repo.  To do so, create a new `User.sublime-commands` file in your `User` Package directory.  Then, add an entry like so:

```json
[
    {
        "caption": "git: pull --rebase",
        "command": "gs_custom",
        "args": {
            "output_to_panel": true,           
            "args": ["pull", "--rebase"],
            "start_msg": "Starting pull (rebase)...",
            "complete_msg": "Pull complete.",
            "run_in_thread": false  # SEE WARNING BELOW
        }
    }
]
```

### Arguments

Your custom command may be further customized by setting the following arguments:

* `output_to_panel` - send the command output to a panel when complete
* `args`            - arguments to pass to the `git` command

      GitSavvy also supports some basic interpolation when specifying your `args`. If one of these strings is provided as an element of your `args` array, the appropriate string will be substituted. The following strings are currently supported:
      
       - `{FILE_PATH}` - the path to the currently opened file.
       - `{REPO_PATH}` - the path to the currently opened file's repo path.

* `start_msg`       - a message to display in status bar when the command starts
* `complete_msg`    - a message to display in status bar when the command completes
* `run_in_thread`   - when true, your command will be run in a separate child thread, independent of the async UI thread

      **:boom: Warning**
  
      Take *extra* care when enabling `run_in_thread`; while it can be useful for long running `git` commands, if handled incorrectly, running such a background thread can have undesirable effects.

## [git-flow](https://github.com/nvie/gitflow) Support

- [flow: init](flow.md#flow-init)
- [flow: feature/release/hotfix/support start](flow.md#flow-featurereleasehotfixsupport-start)
- [flow: feature/release/hotfix/support finish](flow.md#flow-featurereleasehotfixsupport-finish)
- [flow: feature/release/hotfix publish](flow.md#flow-featurereleasehotfix-publish)
- [flow: feature/release track](flow.md#flow-featurerelease-track)
- [flow: feature pull](flow.md#flow-feature-pull)
