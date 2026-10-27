"""
GitHub methods that are functionally separate from anything Sublime-related.
"""

from __future__ import annotations
import http.client
import os
import re
import subprocess
import time
from functools import lru_cache, partial
from typing import Literal, NamedTuple
from webbrowser import open as open_in_browser

from ..common import interwebs
from ..core.exceptions import FailedGithubRequest
from ..core.settings import app_settings
from ..core.utils import STARTUPINFO


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
    token: str | None


@lru_cache(maxsize=128)
def remote_to_url(remote_url: str) -> str:
    """
    Parse out a Github HTTP URL from a remote URI:

    r1 = remote_to_url("git://github.com/timbrel/GitSavvy.git")
    assert r1 == "https://github.com/timbrel/GitSavvy"

    r2 = remote_to_url("git@github.com:timbrel/GitSavvy.git")
    assert r2 == "https://github.com/timbrel/GitSavvy"

    r3 = remote_to_url("https://github.com/timbrel/GitSavvy.git")
    assert r3 == "https://github.com/timbrel/GitSavvy"
    """

    if remote_url.endswith(".git"):
        remote_url = remote_url[:-4]

    if remote_url.startswith("git://"):
        return remote_url.replace("git://", "https://")
    if remote_url.startswith("http"):
        return remote_url
    if url := _transform_ssh_remote_to_https(remote_url):
        return url

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
    token = app_settings.get("api_tokens", {}).get(fqdn) or (
        os.environ.get("GITHUB_TOKEN") if fqdn.lower() == "github.com" else None
    )
    return GitHubRepo(url, fqdn, owner, repo, token)


def _transform_ssh_remote_to_https(remote_url: str) -> str | None:
    match = re.match(r"^ssh://(?:[^@/]+@)?(?P<host>[^/]+)/(?P<path>.+)$", remote_url)
    if not match:
        if "://" in remote_url:
            return None
        match = re.match(r"^(?:[^@/:]+@)?(?P<host>[^:]+):(?P<path>.+)$", remote_url)
        if not match:
            return None

    host, path = match.group("host"), match.group("path").lstrip("/")
    if "/" not in path:
        return None

    return "https://{host}/{path}".format(host=_resolve_ssh_hostname(host), path=path)


@lru_cache(maxsize=128)
def _resolve_ssh_hostname(hostname: str) -> str:
    return _read_ssh_config_hostname(hostname) or hostname


def _read_ssh_config_hostname(hostname: str) -> str | None:
    try:
        output = subprocess.check_output(
            ["ssh", "-G", hostname],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
            startupinfo=STARTUPINFO,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    for line in output.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue

        key, value = parts
        if key.lower() == "hostname" and value:
            return value.strip()

    return None


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


def get_api_fqdn(github_repo: GitHubRepo) -> tuple[bool, str]:
    """
    Determine if the provided GitHub repo object refers to a GitHub
    Enterprise instance or publicly hosted GitHub.com, and indicate
    the base FQDN to use for API requests.
    """
    if github_repo.fqdn.lower() == "github.com":
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


def validate_response(response, method="GET", *, expect_json=True):
    action = {"GET": 'querying', "POST": 'posting to'}[method]

    if response.status in [401, 403]:
        raise FailedGithubRequest(AUTH_ERROR_TEMPLATE.format(action=action))

    if response.status < 200 or response.status > 299 or (expect_json and not response.is_json):
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

    response = interwebs.get(fqdn, 443, path, auth=auth)
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
            if "link" not in response.headers:
                break

            # following next link
            # https://developer.github.com/v3/#pagination
            match = re.match(r'.*<([^>]+)>; rel="next"', response.headers["link"])
            if not match:
                break

            path = match.group(1)

        response = interwebs.get(fqdn, 443, path, auth=auth)
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


def commit_exists(commit: str, github_repo: GitHubRepo) -> bool:
    fqdn, path = github_api_url(f"/repos/{{owner}}/{{repo}}/commits/{commit}", github_repo)
    auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None
    response = interwebs.request(
        "HEAD", fqdn, 443, path, auth=auth,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        })
    # GitHub returns 422 for an unknown SHA, and 404 for a missing repository.
    if response.status in (404, 422):
        return False
    validate_response(response, expect_json=False)
    return True


def get_pull_request_diff(nr: int, github_repo: GitHubRepo) -> str:
    fqdn, path = github_api_url(f"/repos/{{owner}}/{{repo}}/pulls/{nr}", github_repo)
    auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None
    response = interwebs.get(
        fqdn, 443, path, auth=auth,
        headers={
            "Accept": "application/vnd.github.diff",
            "X-GitHub-Api-Version": "2022-11-28"
        })
    validate_response(response, expect_json=False)
    return response.payload.decode("utf-8")


def get_from_github(api_url_template: str, github_repo: GitHubRepo):
    fqdn, path = github_api_url(api_url_template, github_repo)
    auth = (github_repo.token, "x-oauth-basic") if github_repo.token else None

    response = interwebs.get(
        fqdn, 443, path, auth=auth,
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

    response = interwebs.post(fqdn, 443, path, auth=auth, payload=payload)
    validate_response(response, method="POST")

    return response.payload


def create_fork(github_repo: GitHubRepo, default_branch_only: bool = False):
    return post_to_github(
        "/repos/{owner}/{repo}/forks",
        github_repo,
        {"default_branch_only": default_branch_only}
    )


def create_user_repo(token: str, repo_name: str, private: bool = False) -> dict:
    return create_repo(token, None, repo_name, private=private)


def create_repo(token: str, org: str | None, repo_name: str, private: bool = False) -> dict:
    host = "api.github.com"
    path = f"/orgs/{org}/repos" if org else "/user/repos"
    auth = (token, "x-oauth-basic")
    payload = {"name": repo_name, "private": private}

    response = interwebs.post(host, 443, path, auth=auth, payload=payload)
    validate_response(response, method="POST")

    return response.payload


CI_REQUEST_TIMEOUT = 15.0  # [seconds]
DEFAULT_THROTTLE_DELAY = 60.0  # [seconds]

CI_STATUS_QUERY = """
query CiStatus($owner: String!, $name: String!, $oid: GitObjectID!) {
  repository(owner: $owner, name: $name) {
    object(oid: $oid) {
      ... on Commit {
        oid
        statusCheckRollup {
          state
          contexts { totalCount }
        }
      }
    }
  }
}
"""

CI_STATUS_AND_PUSH_TIP_QUERY = """
query CiStatus(
  $owner: String!, $name: String!, $oid: GitObjectID!,
  $pushOwner: String!, $pushName: String!, $pushRef: String!
) {
  repository(owner: $owner, name: $name) {
    object(oid: $oid) {
      ... on Commit {
        oid
        statusCheckRollup {
          state
          contexts { totalCount }
        }
      }
    }
  }
  pushed: repository(owner: $pushOwner, name: $pushName) {
    ref(qualifiedName: $pushRef) { target { oid } }
  }
}
"""

CiState = Literal["success", "failure", "pending", "no_checks", "unknown"]


class CiObservation(NamedTuple):
    state: CiState
    reason: str = ""
    # Only meaningful for "unknown": whether a later attempt may succeed,
    # and how long GitHub asked us to wait before trying again.
    transient: bool = False
    retry_after: float | None = None


class PushTarget(NamedTuple):
    owner: str
    repo: str
    ref: str


def is_github_host(fqdn: str) -> bool:
    """
    Return whether `fqdn` is GitHub.com or an explicitly configured
    GitHub Enterprise host.  Tokens in `api_tokens` alone do not
    identify a host as GitHub as GitLab shares that setting.
    """
    fqdn = fqdn.lower()
    if fqdn == "github.com":
        return True
    enterprise_hosts = app_settings.get("github_enterprise_hosts") or []
    return fqdn in {host.lower() for host in enterprise_hosts if isinstance(host, str)}


def query_ci_status(
    repository: GitHubRepo,
    oid: str,
    push_target: PushTarget | None = None,
    timeout: float = CI_REQUEST_TIMEOUT
) -> CiObservation:
    """
    Observe the commit-check rollup of commit `oid` in `repository`.

    If `push_target` is given, additionally verify in the same request
    that the pushed ref points at `oid`.  Any failure to do so, or any
    other unexpected response, is reported as "unknown", never as a CI
    result.  Never raises.
    """
    if not repository.token:
        return CiObservation("unknown", "no token")

    variables = {"owner": repository.owner, "name": repository.repo, "oid": oid}
    if push_target:
        query = CI_STATUS_AND_PUSH_TIP_QUERY
        variables.update({
            "pushOwner": push_target.owner,
            "pushName": push_target.repo,
            "pushRef": push_target.ref,
        })
    else:
        query = CI_STATUS_QUERY

    host, path = graphql_endpoint(repository)
    try:
        response = interwebs.post(
            host, 443, path,
            payload={"query": query, "variables": variables},
            headers={
                "Authorization": "bearer {}".format(repository.token),
                "Accept": "application/json",
            },
            timeout=timeout
        )
    except (OSError, http.client.HTTPException, ValueError) as e:
        return CiObservation("unknown", "transport error: {}".format(type(e).__name__), transient=True)

    return interpret_ci_response(response, oid, verify_push_tip=push_target is not None)


def graphql_endpoint(repository: GitHubRepo) -> tuple[str, str]:
    if repository.fqdn.lower() == "github.com":
        return "api.github.com", "/graphql"
    return repository.fqdn, "/api/graphql"


def interpret_ci_response(
    response: interwebs.Response, oid: str, verify_push_tip: bool
) -> CiObservation:
    status = response.status
    payload = response.payload
    if status in (403, 429):
        retry_after = _retry_after(response, throttled=status == 429 or _mentions_rate_limit(payload))
        if retry_after is not None:
            return CiObservation("unknown", "rate limited", retry_after=retry_after)
    if status in (401, 403):
        return CiObservation("unknown", "access denied (HTTP {})".format(status))
    if status >= 500:
        return CiObservation("unknown", "server error (HTTP {})".format(status), transient=True)
    if not 200 <= status <= 299 or not response.is_json or not isinstance(payload, dict):
        return CiObservation("unknown", "unexpected response (HTTP {})".format(status))

    errors = payload.get("errors")
    if errors:
        error_types = sorted({
            str(error.get("type")) for error in errors if isinstance(error, dict)
        }) if isinstance(errors, list) else []
        if "RATE_LIMITED" in error_types:
            return CiObservation(
                "unknown", "rate limited", retry_after=_retry_after(response, throttled=True)
            )
        return CiObservation("unknown", "GraphQL errors: {}".format(", ".join(error_types)))

    data = payload.get("data")
    if verify_push_tip:
        tip = _dig(data, "pushed", "ref", "target", "oid")
        if tip != oid:
            return CiObservation("unknown", "pushed ref does not point at the commit")

    commit = _dig(data, "repository", "object")
    if not isinstance(commit, dict) or commit.get("oid") != oid:
        return CiObservation("unknown", "commit not found")

    rollup = commit.get("statusCheckRollup", ...)
    if rollup is None:
        return CiObservation("no_checks")
    count = _dig(rollup, "contexts", "totalCount")
    if not isinstance(count, int) or isinstance(count, bool):
        return CiObservation("unknown", "malformed rollup")
    if count == 0:
        return CiObservation("no_checks")

    state = rollup.get("state")
    if state == "SUCCESS":
        return CiObservation("success")
    if state in ("FAILURE", "ERROR"):
        return CiObservation("failure")
    if state in ("PENDING", "EXPECTED"):
        return CiObservation("pending")
    return CiObservation("unknown", "unexpected rollup state: {}".format(state))


def _retry_after(response: interwebs.Response, throttled: bool) -> float | None:
    headers = response.headers
    retry_after = headers.get("retry-after")
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    if headers.get("x-ratelimit-remaining") == "0":
        try:
            return max(0.0, float(headers["x-ratelimit-reset"]) - time.time())
        except (KeyError, ValueError):
            pass
        throttled = True
    return DEFAULT_THROTTLE_DELAY if throttled else None


def _mentions_rate_limit(payload: object) -> bool:
    message = payload.get("message") if isinstance(payload, dict) else payload
    return isinstance(message, str) and "rate limit" in message.lower()


def _dig(value: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value
