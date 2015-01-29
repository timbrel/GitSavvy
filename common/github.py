import re
from collections import namedtuple
from webbrowser import open as open_in_browser

GitHubRepo = namedtuple("GitHubRepo", ("url", "fqdn", "owner", "repo"))


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
