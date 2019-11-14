# Contribute

If you're interested in adding features, reporting/fixing bugs, or just discussing the future of GitSavvy, this is the place to start.  Please feel free to reach out if you have questions or comments by opening an [issue](https://github.com/timbrel/GitSavvy/issues).


## Adding new features

If there's a feature you'd like to see that isn't already in-progress or documented, there are two ways you can go about it:

- open an issue for discussion; or
- fork and submit a PR.

Either way is fine, so long as you remember your PR may not be accepted as-is or at all.  If a feature request is reasonable, though, it'll most likely be included one way or another.

Some other things to remember:

- Please respect the project layout and hierarchy, to keep things organized.
- All Python code should be PEP8 compliant with few exceptions (e.g. imports in `__init__.py` files).
- Include docstrings for all classes and functions, unless functionality is obvious at a glance.
- Include descriptions for issues and PRs.


## Commit message structure

Please follow the following commit message structure when submitting your pull request to GitSavvy:

    TYPE: Short commit message

    Detailed
    commit
    info

For the value of **`TYPE`**, please use one of **`Feature`**, **`Enhancement`**, or **`Fix`**.

This is done in order to help us automate tasks such as changelog generation.


# Bugs

If you encounter a bug, please check for an open issue that already captures the problem you've run into.  If it doesn't exist yet, create it!

Please include the following information when filing a bug:

- Sublime Text version number
- Git version number
- OS type and version
- Console error output
- A description of the problem.
- Steps to reproduce, if possible
- If the bug is caused by a failing command, [include a debug log](docs/debug.md#providing-a-debug-log).


# Documentation

If you make changes, please remember to update the user documentation to reflect the new behavior.


## Package Testing

Check the implementation details of the [tests](docs/testing.md).

# Publishing

Hotfixes should be submitted to `master` branch. As part of the PR process,
you will be asked to provide the information necessary to make that happen.

Pull requests on new features should be submitted to `dev` branch and will be
regularly merged into `master` after evaluations over an extended period of
time.

# Issue Triage [![Open Source Helpers](https://www.codetriage.com/divmain/gitsavvy/badges/users.svg)](https://www.codetriage.com/divmain/gitsavvy)

You can triage issues which may include reproducing bug reports or asking for vital information, such as version numbers or reproduction instructions. If you would like to start triaging issues, one easy way to get started is to [subscribe to gitsavvy on CodeTriage](https://www.codetriage.com/divmain/gitsavvy).
