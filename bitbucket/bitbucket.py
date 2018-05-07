"""
Bitbucket methods that are functionally separate from anything Sublime-related.
"""

import re
from collections import namedtuple
from functools import partial, lru_cache
from webbrowser import open as open_in_browser

from ..common import interwebs, util
from ..core.exceptions import FailedBitbucketRequest
from ..core.settings import GitSavvySettings


BITBUCKET_PER_PAGE_MAX = 100
BITBUCKET_ERROR_TEMPLATE = "Error {action} Bitbucket: {payload}"
AUTH_ERROR_TEMPLATE = """Error {action} Bitbucket, access was denied!

Please ensure you have created a Bitbucket API token and added it to
your settings, as described in the documentation:

https://github.com/divmain/GitSavvy/blob/master/docs/bitbucket.md#setup
"""

BitbucketRepo = namedtuple("BitbucketRepo", ("url", "fqdn", "owner", "repo", "token"))


@lru_cache()
def remote_to_url(remote):
    """
    Parse out a Bitbucket HTTP URL from a remote URI:

    >>> remote_to_url("git://bitbucket.org/pasha_savchenko/GitSavvy.git")
    'https://bitbucket.org/pasha_savchenko/GitSavvy'

    >>> remote_to_url("git@bitbucket.org:pasha_savchenko/gitsavvy.git")
    'https://bitbucket.org/pasha_savchenko/gitsavvy'

    >>> remote_to_url("https://pasha_savchenko@bitbucket.org/pasha_savchenko/GitSavvy.git")
    'https://pasha_savchenko@bitbucket.org/pasha_savchenko/GitSavvy'
    """

    if remote.endswith(".git"):
        remote = remote[:-4]

    if remote.startswith("git@"):
        return remote.replace(":", "/").replace("git@", "https://")
    elif remote.startswith("git://"):
        return remote.replace("git://", "https://")
    elif remote.startswith("http"):
        return remote
    else:
        util.debug.log_error('Cannot parse remote "%s" to url' % remote)
        return None


@lru_cache()
def parse_remote(remote):
    """
    Given a line of output from `git remote -v`, parse the string and return
    an object with original url, FQDN, owner, repo, and the token to use for
    this particular FQDN (if available).
    """
    url = remote_to_url(remote)
    if not url:
        return None

    match = re.match(r"https?://([a-zA-Z-\.0-9]+)/([a-zA-Z-\._0-9]+)/([a-zA-Z-\._0-9]+)/?", url)

    if not match:
        util.log_error('Invalid Bitbucket url: %s' % url)
        return None

    fqdn, owner, repo = match.groups()

    api_tokens = GitSavvySettings().get("api_tokens")
    token = api_tokens and api_tokens.get(fqdn, None) or None

    return BitbucketRepo(url, fqdn, owner, repo, token)


def open_file_in_browser(rel_path, remote, commit_hash, start_line=None, end_line=None):
    """
    Open the URL corresponding to the provided `rel_path` on `remote`.
    """
    bitbucket_repo = parse_remote(remote)
    if not bitbucket_repo:
        return None

    line_numbers = "#lines-{}:{}".format(start_line, end_line) if start_line is not None else ""

    url = "{repo_url}/src/{commit_hash}/{path}{lines}".format(
        repo_url=bitbucket_repo.url,
        commit_hash=commit_hash,
        path=rel_path,
        lines=line_numbers
    )

    open_in_browser(url)


def open_repo(remote):
    """
    Open the GitHub repo in a new browser window, given the specified remote.
    """
    bitbucket_repo = parse_remote(remote)
    if not bitbucket_repo:
        return None
    open_in_browser(bitbucket_repo.url)


def open_issues(remote):
    """
    Open the GitHub issues in a new browser window, given the specified remote.
    """
    bitbucket_repo = parse_remote(remote)
    if not bitbucket_repo:
        return None
    open_in_browser("{}/issues".format(bitbucket_repo.url))
