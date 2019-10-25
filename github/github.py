"""
GitHub methods that are functionally separate from anything Sublime-related.
"""

import re
from collections import namedtuple
from webbrowser import open as open_in_browser
from functools import partial

from ..common import interwebs, util
from ..core.exceptions import FailedGithubRequest
from ..core.settings import GitSavvySettings


GITHUB_PER_PAGE_MAX = 100
GITHUB_ERROR_TEMPLATE = "Error {action} Github: {payload}"
AUTH_ERROR_TEMPLATE = """Error {action} Github, access was denied!

Please ensure you have created a Github API token and added it to
your settings, as described in the documentation:

https://github.com/timbrel/GitSavvy/blob/master/docs/github.md#setup
"""

GitHubRepo = namedtuple("GitHubRepo", ("url", "fqdn", "owner", "repo", "token"))


def remote_to_url(remote):
    """
    Parse out a Github HTTP URL from a remote URI:

    r1 = remote_to_url("git://github.com/timbrel/GitSavvy.git")
    assert r1 == "https://github.com/timbrel/GitSavvy.git"

    r2 = remote_to_url("git@github.com:divmain/GitSavvy.git")
    assert r2 == "https://github.com/timbrel/GitSavvy.git"

    r3 = remote_to_url("https://github.com/timbrel/GitSavvy.git")
    assert r3 == "https://github.com/timbrel/GitSavvy.git"
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
        util.debug.log_error('Invalid github url: %s' % url)
        return None

    fqdn, owner, repo = match.groups()

    api_tokens = GitSavvySettings().get("api_tokens")
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


def open_repo(remote):
    """
    Open the GitHub repo in a new browser window, given the specified remote.
    """
    github_repo = parse_remote(remote)
    if not github_repo:
        return None
    open_in_browser(github_repo.url)


def open_issues(remote):
    """
    Open the GitHub issues in a new browser window, given the specified remote.
    """
    github_repo = parse_remote(remote)
    if not github_repo:
        return None
    open_in_browser("{}/issues".format(github_repo.url))


def get_api_fqdn(github_repo):
    """
    Determine if the provided GitHub repo object refers to a GitHub-
    Enterprise instance or to publically hosted GitHub.com, and
    indicate what base FQDN to use for API requests.
    """
    if github_repo.fqdn[-10:] == "github.com":
        return False, "api.github.com"
    return True, github_repo.fqdn


def github_api_url(api_url_template, repository, **kwargs):
    """
    Construct a github URL to query using the given url template string,
    and a github.GitHubRepo instance, and optionally query parameters
    of given star-kwargs.

    Return a tuple of: FQDN, PATH
    """
    is_enterprise, fqdn = get_api_fqdn(repository)
    base_path = "/api/v3" if is_enterprise else ""
    request_path = api_url_template.format(
        owner=repository.owner,
        repo=repository.repo
    )
    return fqdn, "{base_path}{path}?{query_params}".format(
        base_path=base_path,
        path=request_path,
        query_params=interwebs.urlencode(kwargs))


def validate_response(response, method="GET"):
    action = {"GET": 'querying', "POST": 'posting to'}[method]

    if response.status in [401, 403]:
        raise FailedGithubRequest(AUTH_ERROR_TEMPLATE.format(action=action))

    if response.status < 200 or response.status > 299 or not response.is_json:
        raise FailedGithubRequest(GITHUB_ERROR_TEMPLATE.format(
            action=action, payload=response.payload))


def query_github(api_url_template, github_repo):
    """
    Takes a URL template that takes `owner` and `repo` template variables
    and as a GitHub repo object.  Do a GET for the provided URL and return
    the response payload, if successful.  If unsuccessfuly raise an error.
    """
    fqdn, path = github_api_url(api_url_template, github_repo)
    auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None

    response = interwebs.get(fqdn, 443, path, https=True, auth=auth)
    validate_response(response)

    return response.payload


get_repo_data = partial(query_github, "/repos/{owner}/{repo}")


def iteratively_query_github(api_url_template, github_repo):
    """
    Like `query_github` but return a generator by repeatedly
    iterating until no link to next page.
    """
    fqdn, path = github_api_url(api_url_template, github_repo,
                                per_page=GITHUB_PER_PAGE_MAX)
    auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None

    response = None

    while True:
        if response is not None:
            # it means this is not the first iter
            if "Link" not in response.headers:
                break

            # following next link
            # https://developer.github.com/v3/#pagination
            match = re.match(r'.*<([^>]+)>; rel="next"', response.headers["Link"])
            if not match:
                break

            path = match.group(1)

        response = interwebs.get(fqdn, 443, path, https=True, auth=auth)
        validate_response(response)

        if response.payload:
            for item in response.payload:
                yield item
        else:
            break


get_issues = partial(iteratively_query_github, "/repos/{owner}/{repo}/issues")
get_contributors = partial(iteratively_query_github, "/repos/{owner}/{repo}/contributors")
get_forks = partial(iteratively_query_github, "/repos/{owner}/{repo}/forks")
get_pull_requests = partial(iteratively_query_github, "/repos/{owner}/{repo}/pulls")


def post_to_github(api_url_template, github_repo):
    """
    Takes a URL template that takes `owner` and `repo` template variables
    and as a GitHub repo object.  Do a POST for the provided URL and return
    the response payload, if successful.  If unsuccessfuly raise an error.
    """
    fqdn, path = github_api_url(api_url_template, github_repo)
    auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None

    response = interwebs.post(fqdn, 443, path, https=True, auth=auth)
    validate_response(response, method="POST")

    return response.payload


create_fork = partial(post_to_github, "/repos/{owner}/{repo}/forks")
