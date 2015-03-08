"""
GitHub methods that are functionally separate from anything Sublime-related.
"""

import re
from collections import namedtuple
from webbrowser import open as open_in_browser
from functools import partial

import sublime

from ..common import interwebs

GitHubRepo = namedtuple("GitHubRepo", ("url", "fqdn", "owner", "repo", "token"))


class FailedGithubRequest(Exception):
    pass


def parse_remote(remote):
    """
    Given a line of output from `git remote -v`, parse the string and return
    an object with original url, FQDN, owner, repo, and the token to use for
    this particular FQDN (if available).
    """
    if remote.endswith(".git"):
        remote = remote[:-4]
        
    if remote.startswith("git@"):
        url = remote.replace(":", "/").replace("git@", "http://")
    elif remote.startswith("http"):
        url = remote
    else:
        return None

    match = re.match(r"https?://([a-zA-Z-\.0-9]+)/([a-zA-Z-\.0-9]+)/([a-zA-Z-\.0-9]+)/?", url)

    if not match:
        return None

    fqdn, owner, repo = match.groups()

    savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
    api_tokens = savvy_settings.get("api_tokens")
    token = api_tokens and api_tokens.get(fqdn, None) or None

    return GitHubRepo(url, fqdn, owner, repo, token)


def open_file_in_browser(rel_path, remote, commit_hash, start_line=None, end_line=None):
    """
    Open the URL corresponding to the provided `rel_path` on `remote`.
    """
    github_repo = parse_remote(remote)
    if not github_repo:
        return None

    line_numbers = "#L{}-L{}".format(start_line, end_line) if start_line is not None else ""

    url = "{repo_url}/blob/{commit_hash}/{path}{lines}".format(
        repo_url=github_repo.url,
        commit_hash=commit_hash,
        path=rel_path,
        lines=line_numbers
    )

    open_in_browser(url)


def get_api_fqdn(github_repo):
    """
    Determine if the provided GitHub repo object refers to a GitHub-
    Enterprise instance or to publically hosted GitHub.com, and
    indicate what base FQDN to use for API requests.
    """
    if github_repo.fqdn[-10:] == "github.com":
        return False, "api.github.com"
    return True, github_repo.fqdn


def query_github(api_url_template, github_repo):
    """
    Takes a URL template that takes `owner` and `repo` template variables
    and as a GitHub repo object.  Do a GET for the provided URL and return
    the response payload, if successful.  If unsuccessfuly raise an error.
    """
    is_enterprise, fqdn = get_api_fqdn(github_repo)
    base_path = "/api/v3" if is_enterprise else ""
    path = base_path + api_url_template.format(
        owner=github_repo.owner,
        repo=github_repo.repo
    )

    auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None

    response = interwebs.get(fqdn, 443, path, https=True, auth=auth)
    if response.status < 200 or response.status > 299 or not response.is_json:
        raise FailedGithubRequest(response.payload)

    return response.payload

get_issues = partial(query_github, "/repos/{owner}/{repo}/issues")
get_contributors = partial(query_github, "/repos/{owner}/{repo}/contributors")
