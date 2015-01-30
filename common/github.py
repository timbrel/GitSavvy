import re
from collections import namedtuple
from webbrowser import open as open_in_browser

from . import interwebs

GitHubRepo = namedtuple("GitHubRepo", ("url", "fqdn", "owner", "repo"))


class FailedGithubRequest(Exception):
    pass


def parse_remote(remote):
    if remote.startswith("git@"):
        url = remote.replace(":", "/").replace("git@", "http://")[:-4]
    elif remote.startswith("http"):
        url = remote[:-4]
    else:
        return None

    match = re.match(r"https?://([a-zA-Z-\.]+)/([a-zA-Z-\.]+)/([a-zA-Z-\.]+)/?", url)

    if not match:
        return None

    fqdn, owner, repo = match.groups()

    return GitHubRepo(url, fqdn, owner, repo)


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
    if github_repo.fqdn[-10:] == "github.com":
        return False, "api.github.com"
    return True, github_repo.fqdn


def get_issues(github_repo):
    is_enterprise, fqdn = get_api_fqdn(github_repo)
    base_path = "/api/v3" if is_enterprise else ""
    path = base_path + "/repos/{owner}/{repo}/issues".format(
        owner=github_repo.owner,
        repo=github_repo.repo
    )
    response = interwebs.get(fqdn, 443, path, https=True)
    if response.status < 200 or response.status > 299 or not response.is_json:
        raise FailedGithubRequest()

    return response.payload
