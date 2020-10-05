# GitSavvy Documentation

You've found GitSavvy's online documentation.

All documentation is in markdown.  Beyond what you might expect, if you see a link to a different page, you can browse to that page by pressing **SUPER-Enter** while the link is under your cursor (**CTRL-Enter** on Windows).

To return to the previous page, press **SUPER-Backspace** (**CTRL-Backspace** on Windows).

If you run into any issues not addressed here, please feel free to [open an issue](https://github.com/timbrel/GitSavvy/issues) and ask for help!


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


### Committing

- [git: commit](commit.md#git-commit)
- [git: commit including unstaged files](commit.md#git-commit-including-unstaged-files)
- [git: amend previous commit](commit.md#git-amend-previous-commit)
- [git: fixup from stage](commit.md#git-fixup-from-stage)
- [git: quick commit](commit.md#git-quick-commit)
- [git: quick stage current file and commit](commit.md#git-quick-stage-current-file-and-commit)
- [git: quick stage current file and fixup](commit.md#git-quick-stage-current-file-and-fixup)


## Stashing

- [git: stash save](stash.md#git_stash_save)
- [git: stash save including untracked files](stash.md#git_stash_save_including_untracked_files)
- [git: stash save staged changes only](stash.md#git_stash_save_staged_changes_only)
- [git: stash show](stash.md#git_stash_show)
- [git: stash apply](stash.md#git_stash_apply)
- [git: stash pop](stash.md#git_stash_pop)
- [git: stash drop](stash.md#git_stash_drop)


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
- [git: smart tag](tag_mgmt.md#git-smart-tag)


### Modifying history

- [git: rebase](rebase.md)
- [git: rebase from terminal](rebase.md#git-rebase-from-terminal)
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
- [git: graph](history.md#git-graph)
- [git: graph current file](history.md#git-graph-current-file)
- [git: compare against ...](history.md#git-compare-against-)
- [git: compare current file against ...](history.md#git-compare-current-file-against-)
- [git: blame current file](history.md#git-blame-current-file)
- [git: reset](misc.md#git-reset)
- [git: reset (reflog)](misc.md#git-reset-reflog)
- [git: reset to branch](misc.md#git-reset-to-branch)
- [git: cherry-pick](misc.md#git-cherry-pick)


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


### Miscellaneous

- [git: mv current file](misc.md#git-mv-current-file)


## Special Views

- [Status view](status.md#overview)
- [Inline-diff view](staging.md#git-diff-current-file-inline)
- [Diff view](staging.md#git-diff)
- [Commit view](commit.md)
- [Blame view](history.md#git-blame-current-file)
- [Log-graph view](history.md#git-graph)


## GitHub Integration

- [github: open file on remote](github.md#github-open-file-on-remote)
- [github: open repo](github.md#github-open-repo)
- [github: open issues](github.md#github-open-issues)
- [github: create pull request](github.md#github-create-pull-request)
- [github: review pull request](github.md#github-review-pull-request)
- [issues integration in commit view](github.md#issues-integration)
- [contributors integration in commit view](github.md#contributors-integration)


## Custom Commands

If you have the need, you can add your own commands that take advantage of GitSavvy's access to your repo. To do so, create a new `User.sublime-commands` file in your `User` Package directory.  Then, add an entry like so:

```javascript
[
    {
        "caption": "git: pull --rebase",
        "command": "gs_custom",
        "args": {
            "output_to_panel": true,
            "args": ["pull", "--rebase"],
            "start_msg": "Starting pull (rebase)...",
            "complete_msg": "Pull complete."
        }
    }
]
```

For more information see [custom commands documentation](custom.md)

## [git-flow](https://github.com/nvie/gitflow) Support

- [flow: init](flow.md#flow-init)
- [flow: feature/release/hotfix/support start](flow.md#flow-featurereleasehotfixsupport-start)
- [flow: feature/release/hotfix/support finish](flow.md#flow-featurereleasehotfixsupport-finish)
- [flow: feature/release/hotfix publish](flow.md#flow-featurereleasehotfix-publish)
- [flow: feature/release track](flow.md#flow-featurerelease-track)
- [flow: feature pull](flow.md#flow-feature-pull)


## Settings

### Settings for Views

For all syntax specific view we have a settings file. These are nothing extra from syntax specific settings. From any view you can click `super+,` on Mac or `ctrl+,` on Windows and Linux.

### Editing Settings

To open the GitSavvy settings, simply run the `Preferences: GitSavvy Settings` command from the command palette. The default settings are documented with helpful inline comments. GitSavvy also supports project specific settings, run the
`Preference: GitSavvy Project Settings` command and add the key `"GitSavvy"` as follows

```
{
    "settings": {
        "GitSavvy":
        {
            // GitSavvy settings go here
        }
    }
}
```

[Preferences Editor](https://packagecontrol.io/packages/Preferences%20Editor) is really good package for editing you settings.

### Key Bindings

GitSavvy's default keyboard shortcuts are defined in the package's `.sublime-keymap` files:

- [Default.sublime-keymap](https://github.com/timbrel/GitSavvy/blob/master/Default.sublime-keymap)
- [Default (Windows).sublime-keymap](https://github.com/timbrel/GitSavvy/blob/master/Default%20(Windows).sublime-keymap)
- [Default (OSX).sublime-keymap](https://github.com/timbrel/GitSavvy/blob/master/Default%20(OSX).sublime-keymap)
- [Default (Linux).sublime-keymap](https://github.com/timbrel/GitSavvy/blob/master/Default%20(Linux).sublime-keymap)

The key bindings can be edited (and new ones added) via [user defined `.sublime-keymap` files](http://docs.sublimetext.info/en/latest/reference/key_bindings.html). These can be accessed easily by running the "Preferences: Key Bindings" command in the command palette.

Here is an example of defining <kbd>ctrl</kbd>+<kbd>shift</kbd>+<kbd>s</kbd> to run the `git: status` dashboard on a MacOS system:

_**${ST3_PACKAGE_DIR}/User/Default (OSX).sublime-keymap**_
```json
[
    { "keys": ["ctrl+shift+s"], "command": "gs_show_status" }
]
```

The full list of GitSavvy's commands can be seen in [Default.sublime-commands](https://github.com/timbrel/GitSavvy/blob/master/Default.sublime-commands).
