from __future__ import annotations
from collections import defaultdict
from collections.abc import Sequence
from functools import partial
from itertools import chain
import re

from GitSavvy.core.fns import filter_, last
from GitSavvy.core.caches import cache_in_store_as
from GitSavvy.core.utils import yes_no_switch
from GitSavvy.core.git_mixins.branches import Upstream


from typing import NamedTuple, TYPE_CHECKING
from typing_extensions import TypeAlias

RemoteName: TypeAlias = str
RemoteUrl: TypeAlias = str

NOTSET = "<NOTSET>"
UPSTREAM_NOT_SET = Upstream("", "", "", "")
# Git's fallback length for abbreviated hashes
DEFAULT_ABBREV = 7

if TYPE_CHECKING:
    from GitSavvy.core.git_command import (BranchesMixin, StatusMixin, _GitCommand)
    class mixin_base(BranchesMixin, StatusMixin, _GitCommand): pass  # noqa: E701
else:
    mixin_base = object


class ConfigEntry(NamedTuple):
    key: str
    value: str


class RemoteInfoBlob(NamedTuple):
    remotes: list[RemoteInfo]
    push_remote: str | None
    integration_remote: str | None


class PushDestination(NamedTuple):
    url: RemoteUrl
    refs: list[PushedRef]


class PushedRef(NamedTuple):
    # One of " " (fast-forward), "+" (forced update), "-" (deleted),
    # "*" (new ref), "!" (rejected), "=" (up to date)
    flag: str
    src: str
    dst: str
    summary: str


class RemoteInfo(NamedTuple):
    name: RemoteName
    # The effective url with `url.<base>.insteadOf` applied, as `git remote -v`
    # shows it.
    url: RemoteUrl
    config: list[ConfigEntry]
    # The url as written in `remote.<name>.url`.
    configured_url: RemoteUrl


class RemotesMixin(mixin_base):

    def get_remotes(self) -> dict[RemoteName, RemoteUrl]:
        """
        Get a list of remotes, provided as tuples of remote name and remote
        url/resource.
        """
        return {
            remote.name: remote.url
            for remote in self.get_remote_info().remotes
        }

    @cache_in_store_as("remote_info")
    def get_remote_info(self) -> RemoteInfoBlob:
        config_entries = self.read_config_entries()
        remote_infos = self._extract_remote_infos(config_entries)
        remotes = {
            remote.name: remote.url
            for remote in remote_infos
        }
        self.update_store({"remotes": remotes})
        remote_names = list(remotes.keys())
        return RemoteInfoBlob(
            remote_infos,
            self.guess_remote_to_push_to(remote_names, config_entries) if remote_names else None,
            self.guess_integration_remote(remote_names, config_entries=config_entries)
        )

    def _extract_remote_infos(self, config_entries: list[ConfigEntry]) -> list[RemoteInfo]:
        remote_configs = self._extract_remote_configs(config_entries)
        return [
            RemoteInfo(
                remote_name,
                rewrite_url(url, config_entries),
                config,
                url
            )
            for remote_name, config in remote_configs.items()
            if (url := last(by_raw_key(config, "url"), None))
        ]

    def _extract_remote_configs(
        self,
        config_entries: list[ConfigEntry]
    ) -> defaultdict[RemoteName, list[ConfigEntry]]:
        remote_configs: defaultdict[RemoteName, list[ConfigEntry]] = defaultdict(list)
        for key, value in config_entries:
            if key.startswith("remote."):
                try:
                    remote_name, subkey = key[7:].rsplit(".", 1)
                except ValueError:
                    continue
                remote_configs[remote_name].append(ConfigEntry(subkey, value))
        return remote_configs

    def read_config_entries(self) -> list[ConfigEntry]:
        return [
            self.parse_config_entry(line)
            for line in self.git(
                "config",
                "--get-regexp",
                r"^(remote\.|gitsavvy\.|url\..*\.insteadof$|.*\.pushdefault$)",
                throw_on_error=False
            ).splitlines()
            if line.strip()
        ]

    def parse_config_entry(self, line: str) -> ConfigEntry:
        try:
            key, value = line.split(maxsplit=1)
        except ValueError:
            key, value = line, ""

        return ConfigEntry(key, value)

    def fetch(self, remote=None, refspec=None, prune=True, local_branch=None, remote_branch=None):
        # type: (str, str, bool, str, str) -> None
        """
        If provided, fetch all changes from `remote`.  Otherwise, fetch
        changes from all remotes.
        """
        if remote is None:
            if refspec is not None:
                raise TypeError("do not set `refspec` when `remote` is `None`")
            if local_branch is not None:
                raise TypeError("do not set `local_branch` when `remote` is `None`")
            if remote_branch is not None:
                raise TypeError("do not set `remote_branch` when `remote` is `None`")
        if refspec is not None:
            if local_branch is not None:
                raise TypeError("do not set `local_branch` when `refspec` is set")
            if remote_branch is not None:
                raise TypeError("do not set `remote_branch` when `refspec` is set")

        if refspec is None:
            refspec = ":".join(filter_((remote_branch, local_branch)))

        self.git(
            "fetch",
            "--prune" if prune else None,
            remote if remote else "--all",
            refspec or None,
        )

    def pull(self, remote=None, remote_branch=None, rebase=None):
        """
        Pull from the specified remote and branch if provided, otherwise
        perform default `git pull`.
        """
        return self.git(
            "pull",
            yes_no_switch("--rebase", rebase),
            remote if remote else None,
            remote_branch if remote and remote_branch else None
        )

    def push(
        self,
        remote: str | None = None,
        branch: str | None = None,
        force: bool = False,
        force_with_lease: bool = False,
        remote_branch: str | None = None,
        set_upstream: bool = False
    ) -> list[PushDestination]:
        """
        Push to the specified remote and branch if provided, otherwise
        perform default `git push`.

        Return what Git reports for each push destination: its effective
        url and the refs it updated.  The output panel still shows Git's
        human-readable report, see `PushPorcelainFormatter`.
        """
        output = self.git(
            "push",
            "--porcelain",
            "--force" if force else None,
            "--force-with-lease" if force_with_lease else None,
            "--set-upstream" if set_upstream else None,
            remote,
            branch if not remote_branch else "{}:{}".format(branch, remote_branch),
            stdout_formatter=PushPorcelainFormatter
        )
        return parse_push_porcelain(output)

    def guess_integration_remote(
        self,
        available_remotes: Sequence[str],
        current_upstream: Upstream | None = UPSTREAM_NOT_SET,
        configured_remote_name: str | None = NOTSET,
        config_entries: list[ConfigEntry] | None = None
    ) -> RemoteName | None:
        if len(available_remotes) == 0:
            return None
        if len(available_remotes) == 1:
            return next(iter(available_remotes))

        if configured_remote_name is NOTSET:
            if config_entries is None:
                config_entries = self.read_config_entries()
            configured_remote_name = last(
                by_raw_key(config_entries, "gitsavvy.ghremote"),
                None
            )
        if configured_remote_name in available_remotes:
            return configured_remote_name

        for remote in ("upstream", "origin"):
            if remote in available_remotes:
                return remote

        if current_upstream is UPSTREAM_NOT_SET:
            current_upstream = self.get_upstream_for_active_branch()
        if current_upstream and current_upstream.remote in available_remotes:
            return current_upstream.remote
        return None

    def guess_remote_to_push_to(
        self,
        available_remotes: Sequence[str],
        config_entries: list[ConfigEntry] | None = None
    ) -> str:
        if len(available_remotes) == 0:
            raise ValueError("guess_remote_to_push_to requires at least one remote")
        if len(available_remotes) == 1:
            return next(iter(available_remotes))

        last_remote_used = self.current_state().get("last_remote_used_for_push")
        if last_remote_used in available_remotes:
            return last_remote_used  # type: ignore[return-value]

        if config_entries is None:
            config_entries = self.read_config_entries()
        get = partial(by_raw_key, config_entries)

        for key in chain(get("gitsavvy.pushdefault"), get("remote.pushdefault"), ["fork", "origin"]):
            if key in available_remotes:
                return key
        return next(iter(available_remotes))

    def username_from_url(self, input_url):
        # URLs can come in one of following formats format
        # https://github.com/timbrel/GitSavvy.git
        #     git@github.com:divmain/GitSavvy.git
        # Kind of funky, but does the job
        _split_url = re.split('/|:', input_url)
        return _split_url[-2] if len(_split_url) >= 2 else ''

    def remotes_containing_commit(self, commit_hash):
        """
        Return a list of remotes which contain a particular commit.
        """
        return list(set([
            branch.split("/")[0]
            for branch in self.branches_containing_commit(commit_hash, remote_only=True)
        ]))

    def guess_default_branch(self, remote_name) -> str | None:
        if contents := self._read_git_file("refs", "remotes", remote_name, "HEAD"):
            # ref: refs/remotes/origin/master
            for line in contents.splitlines():
                line = line.strip()
                if line.startswith("ref: refs/remotes/"):
                    return line[18:]

        try:
            output = self.git(
                "ls-remote", "--symref", remote_name, "HEAD",
                timeout=2.0,
                throw_on_error=False,
                show_panel_on_error=False,
            )
        except Exception:
            return None
        else:
            for line in output.splitlines():
                # ref: refs/heads/master  HEAD
                line = line.strip()
                if line.startswith("ref: refs/heads/"):
                    return line[16:].split()[0]
        return None


def rewrite_url(url: RemoteUrl, config_entries: list[ConfigEntry]) -> RemoteUrl:
    """Apply `url.<base>.insteadOf` like Git does: the longest prefix wins."""
    base, prefix = "", ""
    for key, value in config_entries:
        lowered = key.lower()
        if (
            lowered.startswith("url.")
            and lowered.endswith(".insteadof")
            and url.startswith(value)
            and len(value) > len(prefix)
        ):
            base, prefix = key[len("url."):-len(".insteadof")], value
    return base + url[len(prefix):] if prefix else url


def by_raw_key(config_entries: list[ConfigEntry], raw_key: str) -> list[str]:
    raw_key = raw_key.lower()
    return [
        value
        for key, value in config_entries
        if key.lower() == raw_key
    ]


def parse_push_porcelain(output: str) -> list[PushDestination]:
    """
    Parse the output of `git push --porcelain`: a "To <url>" line per
    destination, each followed by "<flag>\t<src>:<dst>\t<summary>" lines.
    """
    destinations: list[PushDestination] = []
    for line in output.splitlines():
        if line.startswith("To "):
            destinations.append(PushDestination(line[3:], []))
        elif destinations and (ref := parse_pushed_ref(line)):
            destinations[-1].refs.append(ref)
    return destinations


class PushPorcelainFormatter:
    """
    Render the lines of `git push --porcelain` as a plain `git push`
    reports them, line by line as they arrive: up-to-date refs are
    omitted, "To <url>" is held back until its destination shows a ref,
    and the closing "Done" becomes "Everything up-to-date" if all refs
    of the destination were up to date.  Other lines pass through.

    Git pads the summary column to fit the longest abbreviated hash of
    all refs of a destination.  We only see the hashes of the refs so
    far, and assume Git's default abbreviation if there is none yet.
    """

    def __init__(self) -> None:
        self.refs: list[PushedRef] | None = None
        self.header = ""
        self.abbrev = 0

    def __call__(self, line: str) -> str:
        text = line.rstrip("\r\n")
        if text.startswith("To "):
            self.refs, self.header, self.abbrev = [], text + "\n", 0
            return ""
        if self.refs is None:
            return line
        if text == "Done":
            refs, self.refs = self.refs, None
            if all(ref.flag == "=" for ref in refs):
                return "Everything up-to-date\n"
            return ""
        ref = parse_pushed_ref(text)
        if not ref:
            return line
        self.refs.append(ref)
        self.abbrev = max(self.abbrev, abbrev_length(ref.summary))
        if ref.flag == "=":
            return ""
        header, self.header = self.header, ""
        return header + format_pushed_ref(ref, self.abbrev or DEFAULT_ABBREV)


def parse_pushed_ref(line: str) -> PushedRef | None:
    """Parse a "<flag>\\t<src>:<dst>\\t<summary>" line of `git push --porcelain`."""
    if line.count("\t") < 2:
        return None
    flag, refs, summary = line.split("\t", 2)
    src, _, dst = refs.partition(":")
    return PushedRef(flag, src, dst, summary)


def format_pushed_ref(ref: PushedRef, abbrev: int) -> str:
    """Format `ref` like Git's `print_ref_status()` does without --porcelain."""
    summary, _, reason = ref.summary.partition(" (")
    refs = (
        "{} -> {}".format(short_ref_name(ref.src), short_ref_name(ref.dst))
        if ref.src else
        short_ref_name(ref.dst)
    )
    return " {} {:<{}} {}{}\n".format(
        ref.flag, summary, 2 * abbrev + 3, refs, " (" + reason if reason else ""
    )


def abbrev_length(summary: str) -> int:
    """Return the length of the abbreviated hashes in a summary like "a1b2..c3d4"."""
    match = re.match(r"([0-9a-f]+)\.\.\.?([0-9a-f]+)", summary)
    return max(len(match[1]), len(match[2])) if match else 0


def short_ref_name(ref: str) -> str:
    """Strip the prefixes Git's `prettify_refname()` strips."""
    for prefix in ("refs/heads/", "refs/tags/", "refs/remotes/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref
