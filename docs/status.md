# Status

## Overview

GitSavvy's status dashboard view provides:

- an overview of the repo's current state;
- a list of each file that has been added, modified, or deleted, and whether the change has been staged;
- a list of stashes, if available; and
- keyboard shortcuts to perform actions on files, stashes, or the repo

Most of the actions available in the status view can also be performed through the command-palette

To access the status dashboard, open the command-palette and enter `git: status`.  The view will automatically refresh after changes occur and you return to the status view.  If for any reason the view has not updated, you can press `r` to refresh the view.

All keyboard shortcuts are displayed at the bottom of the status screen, with short descriptions of the corresponding action.

## Actions

### Actions for selected file(s)

The following actions are performed against filenames under the cursor.  Unless otherwise noted, each of these commands can be performed against multiple files (via multiple cursors or highlighting a region).

#### Open (`o`)

The file(s) will be opened in the current window.

#### Stage (`s`)

The file(s) will be added to the Git index.

#### Unstage (`u`)

The file(s) will be removed from the Git index.

#### Discard changes (`d`)

Any changes made to the selected file(s), when compared to HEAD, will be discarded.

**WARNING:** This action is not reversable.

#### Open on remote (`h`)

The selected file(s) will be opened at the correct hash in a browser.  At the moment, only GitHub.com and GitHub Enterprise are supported.

#### Launch merge tool (`M`)

If the selected file has a merge conflict, that file will be opened by the external merge tool defined in the Git config.

**Note:** This action can only be performed on a single file at a time.

#### Diff inline (`l`)

A GitSavvy window will be opened to allow you to examine the changes made to a file.  Any additions will be displayed in green and any deletions in red.  You will be able to browse between the hunks of changes made, stage/unstage those hunks, or stage/unstage individual lines.

If the file is staged, the inline diff view will be opened in cached mode.  More information can be found [here](staging.md#git-diff-current-file-inline).


### Actions for all files

The following actions are performed against all applicable files in the repo.

#### Stage all unstaged (`a`)

All unstaged files will be added to the index.  Untracked and ignored files will not be included.

#### Stage all unstaged and untracked (`A`)

All unstaged _and_ untracked files will be added to the index.  Ignored files will not be included.

#### Unstage all staged (`U`)

All staged files will be removed from the index.

#### Discard all unstaged changes (`D`)

Any changes that have not been added to the indexed will be reverted to the state of HEAD.

**WARNING:** This action is not reversable.

#### Diff all (`f`)

A GitSavvy window will be opened to display the diff between the HEAD or index and the working directory.  Once there, you will be able to stage individual hunks.  More information can be found [here](staging.md#git-diff).

#### Diff all cached (`F`)

A GitSavvy window will be opened to display the diff between the working directory and the HEAD or index.  Once there, you will be able to unstage individual hunks.  More information can be found [here](staging.md#git-diff-cached).


### Actions for the repo

#### Commit (`c`)

A GitSavvy window will be opened, where you will be prompted to enter a commit message.  More information can be found [here](commiting.md#git-commit).

#### Commit including unstaged (`C`)

First, any unstaged changes will be automatically staged.  Next, a GitSavvy window will be opened, where you will be prompted to enter a commit message.  More information can be found [here](commiting.md#git-commit-including-unstaged-files).

#### Amend (`m`)

A GitSavvy window will be opened, where you will be prompted to amend the previous commit message.  More information can be found [here](commiting.md#git-amend-previous-commit).

#### Ignore file (`i`)

An entry for the file under the cursor will be added to the repo's root `.gitignore` file.

#### Ignore pattern (`I`)

You will be prompted to enter a pattern to be added to the repo's root `.gitignore` file.  The field will be pre-filled with the path of the currently selected file.


### Actions for stashes

Stashes allow the user to temporarily save and later restore changes that are not ready to be committed.  If any stashes are currently saved, they will be displayed in the status view.

The following actions are applied against the currently selected stash, where applicable.  They use two-letter combinations to activate: just press the indicated keys in the order given.

#### Apply (`t a`)

Given a selected stash, apply the diff to the working tree.

#### Pop (`t p`)

Given a selected stash, apply the diff to the working tree and then delete the stash.

#### Show (`t s`)

The diff for the selected stash will be displayed in another window.

#### Create (`t c`)

You will be prompted to enter a description for the stash.  Once provided, a new stash will be created from all un-committed changes (not including untracked files).  After the changes are captured as a stash, they will be removed from the index and working tree.

#### Create including untracked (`t u`)

Same as the above, except untracked files are included.

#### Discard (`t d`)

Given a selected stash, delete it without applying it.


## Additional notes

When opening the status dashboard, GitSavvy attempts to detect the Git repository for which information should be displayed.  This repo is derived from:

1. the path of the currently open file;
2. the path of the originating file while in a GitSavvy special view; or
3. the first folder added to a project/window.

If none of these lead to a Git repository, an error will be displayed.
