# Staging changes

## `git: diff current file inline`

Running this command will open a special "Inline Diff" view related to the open file.  All changes made to the file will be displayed inline; removed lines are displayed in red, and added lines are displayed in green.  All other lines are displayed as normal, with full syntax highlighting.

While in this view, you can navigate between changed hunks using the `,` and `.` keys.  `,` will take you to the previous hunk and `.` will take you to the next hunk. If you have the `inline_diff_auto_scoll` setting set to `true`, the cursor will be automatically placed on the first hunk when the view is opened.

While the cursor is positioned at a hunk, you can stage that hunk by pressing `h`.  If you'd like to stage a line only, and _not_ the full hunk, move the cursor to the desired line and press `l` (lower-case L).

You also have the option of resetting hunks.  To do so, press `H` (shift-H).  This will cause changes made in that hunk to be removed from the file in the working directory.  You can also reverse individual lines by positioning the cursor and pressing `L`.  Keep in mind that these actions **are** destructive.

If at any time you would like to refresh the view, press `r`.  It will be refreshed automatically whenever you leave the view and then return.

An undo mechanism is also provided.  To undo, press `SUPER-z` on Linux/OSX or `CTRL-z` on Windows.  This is especially useful if you mistakenly reverted a hunk/line and want to get it back.  A full history is kept so that you can undo multiple actions.

However, take note that no guarantees are made here: files can change and reverse-applying a diff may not work properly.  Multiple undos can also have unexpected effects.  Undo is provided as a solution to "oh no, I just did something stupid".

**Note:**  If the file is not present in the index, the comparison is done between the working file and HEAD.  If the file is present in the index, the comparison is done between the working file and the index.


## `git: diff current file inline (cached)`

The "Inline Diff" view in cached mode functions similarly to its counterpart above, with a few important differences.

First, any changes showing up in cache mode will be the inverse of what you see in the standard mode.  For example, if you stage a single hunk in an "Inline Diff" view, that hunk will disappear from the view.  If you then open the "Inline Diff" view in cached mode, only that hunk will appear, and all other changes won't (because they are not staged).

Pressing `h` will unstage the hunk.  `H` is not supported in this view for safety reasons - very rarely would you intentionally unstage a change and also remove it from your working directory.  `l` will unstage a line but, as you might expect, `L` is unsupported.

Browsing between hunks, undo, and resetting the view function as they do in standard inline-diff mode.


## `git: quick stage`

This command will display a quick panel of all files that have been added, deleted, or modified.  By selecting a file, it will be immediately added to the index.

You also have the option of staging all unstaged files (excluding untracked files), and all files (including untracked files).


## `git: diff`

This command will open a special diff view.  Output from `git diff` will be displayed.  If you position your cursor over a hunk and press `SUPER-Enter` (`CTRL-Enter` in Windows), that hunk will be staged.


## `git: diff cached`

This command functions similarly to the above.  However, it displays the output of `git diff --cached` and, when you press `SUPER-Enter` (`CTRL-Enter` in Windows) over a hunk, that hunk will be _removed_ from the index.
