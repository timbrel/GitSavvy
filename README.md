# GitSavvy

Sublime Text 3 plugin providing the following features:

- basic Git functionality; `init`, `add`, `commit`, `amend`, `checkout`, `pull`, `push`, etc.
- inline diff viewing, including quick navigation between modified hunks and the ability to (un)stage files by hunk or by line\
- GitHub integration including issue/collaborator referencing when committing, and opening the current file on GitHub at the selected line
- GitHub-style blame view, showing hunk metadata and ability to view the commit that made the change
- `git diff` view, allowing user to (un)stage hunks across all files
- a status dashboard, exposing much of the available functionality


## Installation

GitSavvy is still alpha software, and not yet available via the Sublime [Package Manager](https://packagecontrol.io/).

### Simple

If you have Package Management installed in Sublime, open your command palette and start typing `Package Control: Add Repository`.  At the prompt, enter the following URL: `https://github.com/divmain/GitSavvy`.  This should keep your version auto-updated with any fixes and changes that are released.

### Less simple

If you want more control over what you pull down, or if you'd like to submit changes, you should pull down the repository directly and restart the editor.

```
# on a Mac
cd "/Users/$(whoami)/Library/Application Support/Sublime Text 3/Packages"
# on Windows (PowerShell)
cd "$env:appdata\Sublime Text 3\Packages\"

git clone git@github.com:divmain/GitSavvy.git
```
