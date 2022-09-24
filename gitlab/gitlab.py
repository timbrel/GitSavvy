"""
GitLab methods that are functionally separate from anything Sublime-related.
"""

import re
from collections import namedtuple
from functools import partial, lru_cache
from webbrowser import open as open_in_browser

from ..common import interwebs, util
from ..core.exceptions import FailedGitLabRequest
from ..core.settings import GitSavvySettings


GITLAB_PER_PAGE_MAX = 100
GITLAB_ERROR_TEMPLATE = "Error {action} GitLab: {payload}"
AUTH_ERROR_TEMPLATE = """Error {action} GitLab, access was denied!

Please ensure you have created a GitLab API token and added it to
your settings, as described in the documentation:

https://github.com/timbrel/GitSavvy/blob/master/docs/gitlab.md#setup
"""

GitLabRepo = namedtuple("GitLabRepo", ("url", "fqdn", "owner", "repo", "token"))


@lru_cache()
def remote_to_url(remote):
    """
    Parse out a GitLab HTTP URL from a remote URI:

    r1 = remote_to_url("git://gitlab.com/asfaltboy/GitSavvy.git")
    assert r1 == "https://gitlab.com/asfaltboy/GitSavvy.git"

    r2 = remote_to_url("git@gitlab.com:asfaltboy/GitSavvy.git")
    assert r2 == "https://gitlab.com/asfaltboy/GitSavvy.git"

    r3 = remote_to_url("https://gitlab.com/asfaltboy/GitSavvy.git")
    assert r3 == "https://gitlab.com/asfaltboy/GitSavvy.git"
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
        util.debug.log_error('Invalid gitlab url: %s' % url)
        return None

    fqdn, owner, repo = match.groups()
    token = GitSavvySettings().get("api_tokens", {}).get(fqdn)
    return GitLabRepo(url, fqdn, owner, repo, token)


def open_file_in_browser(rel_path, remote, commit_hash, start_line=None, end_line=None):
    """
    Open the URL corresponding to the provided `rel_path` on `remote`.
    """
    github_repo = parse_remote(remote)
    if not github_repo:
        return None

    line_numbers = "#L{}-{}".format(start_line, end_line) if start_line is not None else ""

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


def get_api_fqdn(gitlab_repo):
    """
    Determine if the provided GitLab repo object refers to a hosted
    GitLab instance or to publically hosted gitlab.com, and
    indicate what base FQDN to use for API requests.
    """
    if gitlab_repo.fqdn[-10:] == "gitlab.com":
        return False, "api.gitlab.com"
    return True, gitlab_repo.fqdn


def gitlab_api_url(api_url_template, repository, url_params={}, query_params={}):
    """
    Construct a GitLab URL to query using the given url template string,
    and a gitlab.GitLabRepo instance, and optionally query parameters
    of given star-kwargs.

    Return a tuple of: FQDN, PATH
    """
    is_enterprise, fqdn = get_api_fqdn(repository)
    base_path = "/api/v4" if is_enterprise else ""
    # project ID can be specified as an encoded namespaced path:
    # https://docs.gitlab.com/ee/api/README.html#namespaced-path-encoding
    project_id = interwebs.quote('{namespace}/{project_name}'.format(
        namespace=repository.owner, project_name=repository.repo), safe='')
    url_params['project_id'] = project_id
    request_path = api_url_template.format(**url_params)
    return fqdn, "{base_path}{path}?{query_params}".format(
        base_path=base_path,
        path=request_path,
        query_params=interwebs.urlencode(query_params))


def validate_response(response, method="GET"):
    action = {"GET": 'querying', "POST": 'posting to'}[method]

    if response.status in [401, 403]:
        raise FailedGitLabRequest(AUTH_ERROR_TEMPLATE.format(action=action))

    if response.status < 200 or response.status > 299 or not response.is_json:
        raise FailedGitLabRequest(GITLAB_ERROR_TEMPLATE.format(
            action=action, payload=response.payload))


def get_common_kwargs(gitlab_repo):
    """
    Prepare ommon parameters for the request such as port, https
    and authentication headers
    """
    headers = {'Private-Token': gitlab_repo.token} if gitlab_repo.token else None
    return dict(port=443, https=True, headers=headers)


def query_gitlab(api_url_template, gitlab_repo, url_params={}, query_params={}):
    """
    Takes a URL template that takes `owner` and `repo` template variables
    and as a GitLab repo object.  Do a GET for the provided URL and return
    the response payload, if successful.  If unsuccessfuly raise an error.
    """
    fqdn, path = gitlab_api_url(api_url_template, gitlab_repo, url_params, query_params)
    kwargs = get_common_kwargs(gitlab_repo)

    util.debug.add_to_log({
        "type": "debug",
        "error": 'sending gitlab request to {0}, path={1}, '
                 'kwargs: {2}'.format(fqdn, path, kwargs)
    })
    response = interwebs.get(fqdn, path=path, **kwargs)
    validate_response(response)

    return response.payload


# get_repo_data = partial(query_gitlab, "/repos/{owner}/{repo}")


def iteratively_query_gitlab(api_url_template, gitlab_repo, url_params={}, query_params={}):
    """
    Like `query_gitlab` but return a generator by repeatedly
    iterating until no link to next page.
    """
    query_params['per_page'] = GITLAB_PER_PAGE_MAX
    fqdn, path = gitlab_api_url(api_url_template, gitlab_repo, url_params, query_params)
    kwargs = get_common_kwargs(gitlab_repo)

    response = None

    while True:
        if response is not None:
            # it means this is not the first iter
            if "link" not in response.headers:
                break

            # following next link
            # https://docs.gitlab.com/ee/api/README.html#pagination
            match = re.match(r'<([^>]+)>; rel="next"', response.headers["link"])
            if not match:
                break

            path = match.group(1)

        response = interwebs.get(fqdn, path=path, **kwargs)
        validate_response(response)

        if response.payload:
            for item in response.payload:
                yield item
        else:
            break


get_merge_requests = partial(
    iteratively_query_gitlab, "/projects/{project_id}/merge_requests")
get_merge_request_changes = partial(
    query_gitlab,
    "/projects/{project_id}/merge_requests/{mr_id}/changes")
get_merge_request_diff_versions = partial(
    iteratively_query_gitlab,
    "/projects/{project_id}/merge_requests/{mr_id}/versions")
get_merge_request_version_diff = partial(
    query_gitlab,
    "/projects/{project_id}/merge_requests/{mr_id}/versions/{version_id}")
