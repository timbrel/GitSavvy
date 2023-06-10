# GitSavvy

[![tests](https://github.com/timbrel/GitSavvy/actions/workflows/lint.yml/badge.svg)](https://github.com/timbrel/GitSavvy/actions/workflows/lint.yml)
![License](https://camo.githubusercontent.com/890acbdcb87868b382af9a4b1fac507b9659d9bf/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f6c6963656e73652d4d49542d626c75652e737667)


GitSavvy is a powerful plugin for Sublime Text that provides almost all of Git's functionalities within the text editor. It offers a wide range of features that help developers stay productive and efficient by streamlining their Git workflow. Here are some of the main features of GitSavvy:

## Basic Git Functionality
GitSavvy provides all the essential Git commands such as `clone`, `init`, `commit`, `checkout`, `pull`, `push`, `fetch`, and many more.

All of these commands and features can be accessed easily through Sublime's Command Palette (`ctrl+shift+p`). Just start typing the name of the command you want to use, e.g. `checkout`, and Sublime will quickly select the correct command. You can recognize GitSavvy commands as they are all prefixed with `git: `.

After you hit `enter` to select a command, GitSavvy will often start a wizard. For example, typing `checkout` will show a list of available branches, while typing `checkout new branch` will ask for the name of the new branch. Note that Sublime's fuzzy logic helps with typing long commands and remembers acronyms you've used in the Command Palette.  For instance, on my computer, typing `chn` followed by `enter` is enough to create a new branch.

## Special Views for Git Status, Branches, and Tags
GitSavvy provides dedicated views for Git status, branches, and tags. Users can access these views by running the `git: status`, `git: branch` commands.

In addition to these views, GitSavvy also has sophisticated special views for diff, commit, and repository history. But for new users, running the `git: status` command may be the first entry point into the GitSavvy world. From here, you can stage (`s`), unstage (`u`), and discard (`d`) changes to whole files. You can also start the commit phase (`[c]`), open the repository history (`g`), open a diff view (`f`), and push the current branch (`p`).

Most views display keyboard bindings at the bottom, but in some cases, particularly in larger views, not all bindings are shown. If you encounter such a view, you can always press `?` to bring up a help popup, which shows all the available commands and their bindings.

### Usability Note 1

GitSavvy comes with a lot of keyboard bindings, but it doesn't include any *global* shortcuts. This is because adding global shortcuts can conflict with other packages or built-in functionality. However, you can easily add your own global shortcuts by opening Sublime's key bindings file (`Preferences: Key Bindings`).

For example, you can add a global shortcut to open the status view from anywhere by adding the following to your key bindings file:

```
{ "keys": ["ctrl+shift+s"], "command": "gs_show_status"}
```

This will allow you to quickly access the Git status view with the `ctrl+shift+s` shortcut, no matter what file or view you have open. You can add other shortcuts for other GitSavvy commands in a similar way.

### Usability note 2:

After checking the `git status`, the second most common action is probably to make a commit. To make this process more efficient, you can add a global shortcut key to open the commit view from anywhere in Sublime. Simply add the following code to Sublime's keybindings:

```
{ "keys": ["ctrl+shift+c"], "command": "gs_commit"},
```

This will open up the commit view in GitSavvy. If nothing is staged yet, it will assume the `-a` flag and select all changes. However, it will not include untracked files as is typical with Git. Within the commit view, you can always see the diff of the changes to be committed at the bottom. Here, you can easily unstage or discard hunks or single lines directly using the keyboard shortcuts `u` and `d`.  Note that you can undo a discard by pressing `ctrl+z`.


### Pro-tip: Finding and Running GitSavvy Commands

Sometimes you may want to find out which commands to use, along with their optional arguments. You can use Sublime's console, which is a Python REPL, to help you with this.

To open the console in Sublime, press ``Ctrl + ` `` on Windows or Linux, or ``Cmd + ` `` on a Mac. This will open a panel at the bottom of the window where you can enter commands.

To enable logging of commands, enter the following command in the console:

```python
sublime.log_commands(True)
```

This will cause Sublime to log all commands executed by the user in the console.

Now, start GitSavvy's Command Palette (`ctrl+shift+p`) and run a command, such as `git: status`. After the command has executed, look at the console to see the name of the command and any optional arguments that were passed.

For example, you might see:

```
command: gs_commit {"amend": true}
```

Here, `gs_commit` is the name of the command, and the optional argument passed was `{"amend": true}`. This information can be useful for learning and customizing GitSavvy's functionality.

Don't forget to turn off command logging when you're done, by entering:

```python
sublime.log_commands(False)
```

in the console.


## Repo History

GitSavvy's `Repo History` view provides a visual representation of a repository's commit history and is accessible via the `git: Repo History` command. It's similar to GitK but with more features. When editing a tracked file, you can alternatively use `git: File History` to open a file-specific history view. But you can also turn any standard `Repo History` view into a file/folder history view by using `l` which opens up a list of all tracked files.

The `Repo History` view offers additional filtering options. Press `f` to open the filter prompt, where you can add filters like `--author=` or `-Sfoobar`. You can use the up and down arrow keys to access a list of default filters or to see the history of filters you've previously used. Once you've filtered the graph, you can quickly toggle the filter on and off using `F`.

To move around the commit graph, you can either use your mouse or the up and down arrow keys. If you want to make larger jumps in the graph, you can use the keyboard shortcut `alt+up` or `alt+down`, which jumps through the "first-parent" only.

The `Repo History` view has two main menus. Pressing `enter` will open the standard menu, which provides typical functionality such as checking out, cherry-picking, or reverting the currently selected commit. You can also find basic branch and tag functionality like creating or deleting them. Additionally, you can move branch pointers around (as opposed to resetting) or update branches from their upstream.

You can access a second menu by pressing `r`, which is specifically designed for rebasing. This menu allows you to reword commit messages (`W`), edit (`E`) or drop commits, apply fixups, and generally rebase anything onto anything. For example, you can extract a series of commits onto your main branch to make two feature branches out of a convoluted one.


## Line History

GitSavvy has a feature called "Line History," also known as git's "wtf?" view. This allows you to view the commit history of specific lines within the repository. It's a great way to quickly research the history of a particular piece of code before using the traditional `blame` view.

To access Line History, you can start on any normal view by selecting some lines, such as a function, and then issuing the `git: Line History` command from the command palette.  But really, you can start on any diff/patch that GitSavvy shows you.

To make it more seamless, I recommend, again, to make a shortcut. For example, you can add the following to your user keybindings:

```
  { "keys": ["ctrl+shift+l"], "command": "gs_line_history"},
```

Once you have that, you can use `ctrl+l` to select some lines, followed by `ctrl+shift+l` to follow those lines. Just Like That.

GitSavvy will now show you snippets of commits/patches to show you the evolution of those lines.  Pro-tip: If you're on a diff, not selecting any lines will select the current hunk, giving you the "hunk-history", so to speak.

So why is Line History useful? Research, and beyond that: Well, let's say you're doing a fixup and you realize there is a `console.log` committed. You can remove the line, save your changes, enter `git: diff`, and stage the line. Then, use `ctrl+shift+l` to follow the hunk history and search for the commit that introduced the `console.log` (probably the first one). You can use `f` to start a fixup commit for that exact commit, and `ctrl+enter` to commit your changes. And that's it!


## Git Diff View
GitSavvy provides a `git diff` view that allows users to stage, unstage, and reset (discard) files, hunks, or individual lines.

## Fixup/Squash Helpers
GitSavvy offers fixup/squash helpers that can be accessed from various views, including the "Line History" view.

## GitHub Integration
GitSavvy provides GitHub integration that allows users to reference issues/collaborators when committing, open the current file or a commit on GitHub at the selected line, and create a new pull request from the current branch.

## GitHub-Style Blame View
GitSavvy offers a "blame" view that shows hunk metadata and allows users to view the commit that made the change, similar to GitHub's blame view.



**Note:** GitSavvy requires Git versions at or greater than 2.18.0.

**Note:** Sublime Text 2 is not supported.  Also, GitSavvy takes advantage of modern features of Sublime Text (like annotations).  For the best experience, use the latest Sublime Text _dev_ build.  Or not, I mean it could also crash you, what am I to recommend here.


## Documentation

The documentation is probably outdated.  Yeah it's sad but you can contribute and I will eventually get onto it **but** every special view has help available, just press `?`.

Feature documentation can be found [here](docs/README.md).  It can also be accessed from within Sublime by opening the command palette and typing `GitSavvy: help`.


## Highlights

<table>
    <tr>
        <th>Inline-diff</th>
        <th>Status dashboard</th>
    </tr>
    <tr>
        <td width="50%">
            <a href="https://cloud.githubusercontent.com/assets/5016978/6471628/886430f8-c1a1-11e4-99e9-883837dba86f.gif">
                <img src="https://cloud.githubusercontent.com/assets/5016978/6471628/886430f8-c1a1-11e4-99e9-883837dba86f.gif" width="100%">
            </a>
        </td>
        <td width="50%">
            <a href="https://cloud.githubusercontent.com/assets/5016978/6704171/2f236466-cd02-11e4-9b7d-22cc880b5e9d.png">
                <img src="https://cloud.githubusercontent.com/assets/5016978/6704171/2f236466-cd02-11e4-9b7d-22cc880b5e9d.png" width="100%">
            </a>
        </td>
    </tr>
    <tr>
        <td width="50%">(Un)stage and revert individual lines and hunks.</td>
        <td width="50%">Display and overview and offer actions to manipulate your project state.</td>
    </tr>
</table>

<table>
    <tr>
        <th>Branch dashboard</th>
        <th>Tags dashboard</th>
    </tr>
    <tr>
        <td width="50%">
            <a href="https://cloud.githubusercontent.com/assets/5016978/6704168/2b2e7b84-cd02-11e4-90f4-8dd96b21edeb.png">
                <img src="https://cloud.githubusercontent.com/assets/5016978/6704168/2b2e7b84-cd02-11e4-90f4-8dd96b21edeb.png" width="100%">
            </a>
        </td>
        <td width="50%">
            <a href="https://cloud.githubusercontent.com/assets/5016978/6704169/2c80beac-cd02-11e4-8940-986ea0f0d6bb.png">
                <img src="https://cloud.githubusercontent.com/assets/5016978/6704169/2c80beac-cd02-11e4-8940-986ea0f0d6bb.png" width="100%">
            </a>
        </td>
    </tr>
    <tr>
        <td width="50%">View and manipulate local and remote branches.</td>
        <td width="50%">View and manipulate local and remote tags.</td>
    </tr>
</table>

<table>
    <tr>
        <th>Github integration</th>
        <th>Rebase dashboard</th>
    </tr>
    <tr>
        <td width="50%">
            <a href="https://cloud.githubusercontent.com/assets/5016978/6704029/8fcaddbe-cd00-11e4-83b6-32276a2c2b65.gif">
                <img src="https://cloud.githubusercontent.com/assets/5016978/6704029/8fcaddbe-cd00-11e4-83b6-32276a2c2b65.gif" width="100%">
            </a>
        </td>
        <td width="50%">
            <a href="https://cloud.githubusercontent.com/assets/5016978/7017776/5ca9ceca-dcb1-11e4-8fcb-552551f7743a.gif">
                <img src="https://cloud.githubusercontent.com/assets/5016978/7017776/5ca9ceca-dcb1-11e4-8fcb-552551f7743a.gif" width="100%">
            </a>
        </td>
    </tr>
    <tr>
        <td width="50%">Reference issues and collaborators in commits.  Open files on GitHub in the browser, with lines pre-selected.</td>
        <td width="50%"> Squash, edit, move, rebase, undo, redo.</td>
    </tr>
</table>


## Installation

### Simple

1. Install the [Sublime Text Package Control](https://packagecontrol.io/) plugin if you don't have it already.
2. Open the command palette and start typing `Package Control: Install Package`.
3. Enter `GitSavvy`.


### Less simple

If you want more control over what you pull down, or if you'd like to submit changes to GitSavvy, you should pull down the repository directly in the Packages folder and restart the editor.  You still have to run `Package Control: Satisfy Dependencies` after that!
