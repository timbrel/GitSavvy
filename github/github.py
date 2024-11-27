"""
GitHub methods that are functionally separate from anything Sublime-related.
"""

from __future__ import annotations
import re
from webbrowser import open as open_in_browser
from functools import partial
from typing import NamedTuple

from ..common import interwebs
from ..core.exceptions import FailedGithubRequest
from ..core.settings import GitSavvySettings


GITHUB_PER_PAGE_MAX = 100
GITHUB_ERROR_TEMPLATE = "Error {action} Github: {payload}"
AUTH_ERROR_TEMPLATE = """Error {action} Github, access was denied!

Please ensure you have created a Github API token and added it to
your settings, as described in the documentation:

https://github.com/timbrel/GitSavvy/blob/master/docs/github.md#setup
"""


class GitHubRepo(NamedTuple):
    url: str
    fqdn: str
    owner: str
    repo: str
    token: str


def remote_to_url(remote_url: str) -> str:
    """
    Parse out a Github HTTP URL from a remote URI:

    r1 = remote_to_url("git://github.com/timbrel/GitSavvy.git")
    assert r1 == "https://github.com/timbrel/GitSavvy"

    r2 = remote_to_url("git@github.com:divmain/GitSavvy.git")
    assert r2 == "https://github.com/timbrel/GitSavvy"

    r3 = remote_to_url("https://github.com/timbrel/GitSavvy.git")
    assert r3 == "https://github.com/timbrel/GitSavvy"
    """

    if remote_url.endswith(".git"):
        remote_url = remote_url[:-4]

    if remote_url.startswith("git@"):
        return remote_url.replace(":", "/").replace("git@", "https://")
    elif remote_url.startswith("git://"):
        return remote_url.replace("git://", "https://")
    elif remote_url.startswith("http"):
        return remote_url
    else:
        raise ValueError('Cannot parse remote "{}" and transform to url'.format(remote_url))


def parse_remote(remote_url: str) -> GitHubRepo:
    """
    Given a line of output from `git remote -v`, parse the string and return
    an object with original url, FQDN, owner, repo, and the token to use for
    this particular FQDN (if available).
    """
    url = remote_to_url(remote_url)

    match = re.match(r"https?://([a-zA-Z-\.0-9]+)/([a-zA-Z-\._0-9]+)/([a-zA-Z-\._0-9]+)/?", url)
    if not match:
        raise ValueError("Invalid github url: {}".format(url))

    fqdn, owner, repo = match.groups()
    token = GitSavvySettings().get("api_tokens", {}).get(fqdn)
    return GitHubRepo(url, fqdn, owner, repo, token)


def construct_github_file_url(rel_path, remote_url, commit_hash, start_line=None, end_line=None) -> str:
    """
    Open the URL corresponding to the provided `rel_path` on `remote_url`.
    """
    github_repo = parse_remote(remote_url)
    line_numbers = "#L{}-L{}".format(start_line, end_line) if start_line is not None else ""

    return "{repo_url}/blob/{commit_hash}/{path}{lines}".format(
        repo_url=github_repo.url,
        commit_hash=commit_hash,
        path=rel_path,
        lines=line_numbers
    )


def open_repo(remote_url):
    """
    Open the GitHub repo in a new browser window, given the specified remote url.
    """
    github_repo = parse_remote(remote_url)
    open_in_browser(github_repo.url)


def open_issues(remote_url):
    """
    Open the GitHub issues in a new browser window, given the specified remote url.
    """
    github_repo = parse_remote(remote_url)
    open_in_browser("{}/issues".format(github_repo.url))


def get_api_fqdn(github_repo):
    """
    Determine if the provided GitHub repo object refers to a GitHub
    Enterprise instance or publicly hosted GitHub.com, and indicate
    the base FQDN to use for API requests.
    """
    if github_repo.fqdn[-10:] == "github.com":
        return False, "api.github.com"
    return True, github_repo.fqdn


def github_api_url(
    api_url_template: str, repository: GitHubRepo, **kwargs: dict[str, str]
) -> tuple[str, str]:
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


def query_github(api_url_template: str, github_repo: GitHubRepo):
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


def iteratively_query_github(
    api_url_template: str, github_repo: GitHubRepo, query: dict = {}, yield_: str = None
):
    """
    Like `query_github` but return a generator by repeatedly
    iterating until no link to next page.
    """
    default_query = {"per_page": GITHUB_PER_PAGE_MAX}
    query_ = {**default_query, **query}
    fqdn, path = github_api_url(api_url_template, github_repo, **query_)
    auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None

    response = None

    while True:
        if response is not None:
            # it means this is not the first iter
            if "link" not in response.headers:  # type: ignore[unreachable]
                break

            # following next link
            # https://developer.github.com/v3/#pagination
            match = re.match(r'.*<([^>]+)>; rel="next"', response.headers["link"])
            if not match:
                break

            path = match.group(1)

        response = interwebs.get(fqdn, 443, path, https=True, auth=auth)
        validate_response(response)

        if response.payload:
            if yield_:
                yield from response.payload[yield_]
            else:
                yield from response.payload
        else:
            break


get_issues = partial(iteratively_query_github, "/repos/{owner}/{repo}/issues")
get_contributors = partial(iteratively_query_github, "/repos/{owner}/{repo}/contributors")
get_forks = partial(iteratively_query_github, "/repos/{owner}/{repo}/forks")
get_pull_requests = partial(iteratively_query_github, "/repos/{owner}/{repo}/pulls")


def search_pull_requests(repository: GitHubRepo, q: str):
    return iteratively_query_github("/search/issues", repository, query={"q": q}, yield_="items")


def get_pull_request(nr: str | int, github_repo: GitHubRepo):
    return get_from_github(f"/repos/{{owner}}/{{repo}}/pulls/{nr}", github_repo)


def get_from_github(api_url_template: str, github_repo: GitHubRepo):
    fqdn, path = github_api_url(api_url_template, github_repo)
    auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None

    response = interwebs.get(
        fqdn, 443, path, https=True, auth=auth,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        })
    validate_response(response)
    return response.payload


def post_to_github(api_url_template, github_repo, payload=None):
    """
    Takes a URL template that takes `owner` and `repo` template variables
    and as a GitHub repo object.  Do a POST for the provided URL and return
    the response payload, if successful.  If unsuccessfuly raise an error.
    """
    fqdn, path = github_api_url(api_url_template, github_repo)
    auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None

    response = interwebs.post(fqdn, 443, path, https=True, auth=auth, payload=payload)
    validate_response(response, method="POST")

    return response.payload


def create_fork(github_repo: GitHubRepo, default_branch_only: bool = False):
    return post_to_github(
        "/repos/{owner}/{repo}/forks",
        github_repo,
        {"default_branch_only": default_branch_only}
    )


def create_user_repo(token: str, repo_name: str) -> dict:
    return create_repo(token, None, repo_name)


def create_repo(token: str, org: str | None, repo_name: str) -> dict:
    host = "api.github.com"
    path = f"/orgs/{org}/repos" if org else "/user/repos"
    auth = (token, "x-oauth-basic")
    payload = {"name": repo_name}

    response = interwebs.post(host, 443, path, https=True, auth=auth, payload=payload)
    validate_response(response, method="POST")

    return response.payload
