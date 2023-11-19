from contextlib import contextmanager
from functools import partial
import os
import re

from sublime_plugin import WindowCommand

from ..commands import GsNavigate
from ...common import ui
from ..git_command import GitCommand, GitSavvyError
from ..git_mixins.tags import TagList
from ...common import util
from GitSavvy.core.fns import filter_
from GitSavvy.core.runtime import enqueue_on_worker, on_worker, run_on_new_thread
from GitSavvy.core.utils import flash, uprint
from GitSavvy.core.ui_mixins.quick_panel import show_remote_panel


__all__ = (
    "gs_show_tags",
    "gs_tags_toggle_remotes",
    "gs_tags_refresh",
    "gs_tags_delete",
    "gs_tags_push",
    "gs_tags_show_commit",
    "gs_tags_show_graph",
    "gs_tags_navigate_tag",
)


MYPY = False
if MYPY:
    from typing import Dict, Iterator, List, Literal, Optional, Union, TypedDict
    from ..git_mixins.active_branch import Commit
    from ..git_mixins.tags import TagDetails

    Loading = TypedDict("Loading", {"state": Literal["loading"]})
    Erred = TypedDict("Erred", {"state": Literal["erred"], "message": str})
    Succeeded = TypedDict("Succeeded", {
        "state": Literal["succeeded"],
        "tags": List[TagDetails]
    })
    FetchStateMachine = Union[
        Loading, Erred, Succeeded
    ]

    TagsViewState = TypedDict(
        "TagsViewState",
        {
            "git_root": str,
            "long_status": str,
            "local_tags": TagList,
            "remotes": Dict[str, str],
            "remote_tags": Dict[str, FetchStateMachine],
            "recent_commits": List[Commit],
            "max_items": Optional[int],
            "show_remotes": bool,
            "show_help": bool,
        },
        total=False
    )


NO_LOCAL_TAGS_MESSAGE = "    Your repository has no tags."
NO_REMOTE_TAGS_MESSAGE = "    The remote has no tags."
NO_MORE_TAGS_MESSAGE = "    No further tags on the remote."
REMOTE_ERRED = "    Unable to retrieve tags for this remote."
LOADING_TAGS_MESSAGE = "    Loading tags from remote..."

START_PUSH_MESSAGE = "Pushing tag..."
END_PUSH_MESSAGE = "Push complete."
TAG_DELETE_MESSAGE = "Tag(s) deleted."


class gs_show_tags(WindowCommand, GitCommand):

    """
    Open a branch dashboard for the active Git repository.
    """

    def run(self):
        ui.show_interface(self.window, self.repo_path, "tags")


class TagsInterface(ui.ReactiveInterface, GitCommand):

    """
    Tags dashboard.
    """

    interface_type = "tags"
    syntax_file = "Packages/GitSavvy/syntax/tags.sublime-syntax"

    template = """\

      ROOT:    {git_root}

      BRANCH:  {branch_status}
      HEAD:    {head}

      LOCAL:
    {local_tags}
    {remote_tags}
    {< help}
    """
    template_help = """
      #############                   ###########
      ## ACTIONS ##                   ## OTHER ##
      #############                   ###########

      [c] create                      [r]         refresh dashboard
      [s] create smart tag            [?]         toggle this help menu
      [d] delete                      [e]         toggle display of remote branches
      [p] push to remote              [tab]       transition to next dashboard
                                      [SHIFT-tab] transition to previous dashboard
      [o] show commit
      [g] show log graph

    -
    """

    template_remote = """
      REMOTE ({remote_name}):
    {remote_tags_list}"""

    subscribe_to = {"local_tags", "long_status", "recent_commits", "remotes"}
    state = {}  # type: TagsViewState

    def __init__(self, *args, **kwargs):
        self.state = {
            'show_remotes': self.savvy_settings.get("show_remotes_in_branch_dashboard"),
            'remote_tags': {}
        }
        super().__init__(*args, **kwargs)

    def title(self):
        # type: () -> str
        return "TAGS: {}".format(os.path.basename(self.repo_path))

    def refresh_view_state(self):
        # type: () -> None
        enqueue_on_worker(self.get_local_tags)
        enqueue_on_worker(self.get_latest_commits)
        enqueue_on_worker(self.get_remotes)
        if self.state.get("remotes") is None:
            # run after the `self.get_remotes` above!
            enqueue_on_worker(self.maybe_populate_remote_tags)
        else:
            self.maybe_populate_remote_tags()
        self.view.run_command("gs_update_status")

        self.update_state({
            'git_root': self.short_repo_path,
            'max_items': self.savvy_settings.get("max_items_in_tags_dashboard", None),
            'show_help': not self.view.settings().get("git_savvy.help_hidden"),
        })

    @ui.inject_state()
    def maybe_populate_remote_tags(self, remotes, show_remotes, remote_tags):
        # type: (Dict[str, str], bool, Dict[str, FetchStateMachine]) -> None
        def do_tags_fetch(remote_name):
            try:
                new_state = {
                    "state": "succeeded",
                    "tags": list(self.get_remote_tags(remote_name).all)
                }  # type: FetchStateMachine
            except GitSavvyError as e:
                new_state = {
                    "state": "erred",
                    "message": "    {}".format(e.stderr.rstrip())
                }

            def sink():
                remote_tags[remote_name] = new_state
                self.just_render()
            enqueue_on_worker(sink)  # fan-in

        if remotes and not remote_tags:
            for remote_name in remotes:
                run_on_new_thread(do_tags_fetch, remote_name)    # fan-out
                remote_tags[remote_name] = {
                    "state": "loading"
                }

    @contextmanager
    def keep_cursor_on_something(self):
        # type: () -> Iterator[None]
        on_special_symbol = partial(self.cursor_is_on_something, "meta.git-savvy.tags.tag")

        yield
        if not on_special_symbol():
            self.view.run_command("gs_tags_navigate_tag")

    @ui.section("branch_status")
    def render_branch_status(self, long_status):
        # type: (str) -> ui.RenderFnReturnType
        return long_status

    @ui.section("git_root")
    def render_git_root(self, git_root):
        # type: (str) -> ui.RenderFnReturnType
        return git_root

    @ui.section("head")
    def render_head(self, recent_commits):
        # type: (List[Commit]) -> ui.RenderFnReturnType
        if not recent_commits:
            return "No commits yet."

        return "{0.hash} {0.message}".format(recent_commits[0])

    @ui.section("local_tags")
    def render_local_tags(self, local_tags, max_items):
        # type: (TagList, int) -> ui.RenderFnReturnType
        if not any(local_tags.all):
            return NO_LOCAL_TAGS_MESSAGE

        remote_tags, remote_tag_names = set(), set()
        # wait until all settled to prohibit intermediate state to be drawn
        # what we draw explicitly relies on *all* known remote tags
        if all(info["state"] != "loading" for info in self.state["remote_tags"].values()):
            for info in self.state["remote_tags"].values():
                if info["state"] == "succeeded":
                    for tag in info["tags"]:
                        remote_tags.add((tag.sha, tag.tag))
                        remote_tag_names.add(tag.tag)

        def maybe_mark(tag):
            if remote_tag_names and tag.tag not in remote_tag_names:
                return "*"  # denote new semver
            if remote_tags and (tag.sha, tag.tag) not in remote_tags:
                return "!"  # denote known semver on a different hash
            return " "

        return "\n{}\n".format(" " * 60).join(  # need some spaces on the separator line otherwise
                                                # the syntax expects the remote section begins
            filter_((
                "\n".join(
                    "    {} {}".format(
                        self.get_short_hash(tag.sha),
                        tag.tag,
                    )
                    for tag in local_tags.regular[:max_items]
                ),
                "\n".join(
                    "   {}{} {:<10} {}{}".format(
                        maybe_mark(tag),
                        self.get_short_hash(tag.sha),
                        tag.tag,
                        tag.human_date,
                        " ({})".format(tag.relative_date) if tag.relative_date != tag.human_date else ""
                    )
                    for tag in local_tags.versions[:max_items]
                )
            ))
        )

    @ui.section("remote_tags")
    def render_remote_tags(self, remotes, show_remotes, remote_tags):
        # type: (Dict[str, str], bool, Dict[str, FetchStateMachine]) -> ui.RenderFnReturnType
        if not remotes:
            return "\n"

        if not show_remotes:
            return self.render_remote_tags_off()

        output_tmpl = ""
        render_fns = []

        for remote_name in remotes:
            remote_info = remote_tags.get(remote_name)
            if not remote_info:
                continue

            tmpl_key = "remote_tags_list_" + remote_name
            output_tmpl += "{" + tmpl_key + "}\n"

            @ui.section(tmpl_key)
            def render_remote(remote_name=remote_name, remote_info=remote_info) -> str:
                return self.get_remote_tags_list(remote_name, remote_info)

            render_fns.append(render_remote)

        return output_tmpl, render_fns

    @ui.section("help")
    def render_help(self, show_help):
        # type: (bool) -> ui.RenderFnReturnType
        if not show_help:
            return ""
        return self.template_help

    @ui.inject_state()
    def get_remote_tags_list(self, remote_name, remote_info, local_tags, max_items):
        # type: (str, FetchStateMachine, TagList, int) -> str
        if remote_info["state"] == "succeeded":
            if remote_info["tags"]:
                seen = {(tag.sha, tag.tag) for tag in local_tags.all}
                tags_list = [
                    tag
                    for tag in remote_info["tags"]
                    if tag.tag[-3:] != "^{}" and (tag.sha, tag.tag) not in seen
                ]
                msg = "\n".join(
                    "    {} {}".format(self.get_short_hash(tag.sha), tag.tag)
                    for tag in tags_list[:max_items]
                ) or NO_MORE_TAGS_MESSAGE

            else:
                msg = NO_REMOTE_TAGS_MESSAGE

        elif remote_info["state"] == "erred":
            msg = remote_info["message"]

        elif remote_info["state"] == "loading":
            msg = LOADING_TAGS_MESSAGE

        return self.template_remote.format(
            remote_name=remote_name,
            remote_tags_list=msg
        )

    def render_remote_tags_off(self):
        # type: () -> str
        return "\n  ** Press [e] to toggle display of remote branches. **\n"


TAGS_SELECTOR = "meta.git-savvy.tag.name"
SHA_SELECTOR = "constant.other.git-savvy.tags.sha1"


class TagsInterfaceCommand(ui.InterfaceCommand):
    interface_type = TagsInterface
    interface = None  # type: TagsInterface

    def selected_local_tags(self):
        # type: () -> List[str]
        return ui.extract_by_selector(
            self.view, TAGS_SELECTOR, self.region_name_for("local_tags"))

    def selected_local_commits(self):
        # type: () -> List[str]
        return ui.extract_by_selector(
            self.view, SHA_SELECTOR, self.region_name_for("local_tags"))

    def selected_remote_tags(self, remote_name):
        # type: (str) -> List[str]
        return ui.extract_by_selector(
            self.view, TAGS_SELECTOR, self.remote_section_name_for(remote_name))

    def selected_remote_commits(self, remote_name):
        # type: (str) -> List[str]
        return ui.extract_by_selector(
            self.view, SHA_SELECTOR, self.remote_section_name_for(remote_name))

    def remote_section_name_for(self, remote_name):
        # type: (str) -> str
        return self.region_name_for("remote_tags_list_" + remote_name)


class gs_tags_toggle_remotes(TagsInterfaceCommand):

    """
    Toggle display of the remote tags.
    """

    def run(self, edit, show=None):
        interface = self.interface
        current_state = interface.state["show_remotes"]
        next_state = not current_state if show is None else show
        interface.state["show_remotes"] = next_state
        if next_state:
            interface.state["remote_tags"] = {}
        interface.render()


class gs_tags_refresh(TagsInterfaceCommand):

    """
    Refresh the tags dashboard.
    """

    def run(self, edit, reset_remotes=False):
        interface = self.interface
        if reset_remotes:
            interface.state["remote_tags"] = {}

        util.view.refresh_gitsavvy(self.view)


DELETE_UNDO_MESSAGE = """\
GitSavvy: Deleted tag ({0}), in case you want to undo, run:
  $ git tag {0} {1}
"""
EXTRACT_COMMIT = re.compile(r"\(was (.+)\)")


class gs_tags_delete(TagsInterfaceCommand):

    """
    Delete selected tag(s).
    """

    @on_worker
    def run(self, edit):
        interface = self.interface
        self.delete_local()
        self.delete_remote(interface)
        util.view.refresh_gitsavvy(self.view)

    def delete_local(self):
        tags_to_delete = self.selected_local_tags()
        if not tags_to_delete:
            return

        for tag in tags_to_delete:
            rv = self.git("tag", "-d", tag)
            match = EXTRACT_COMMIT.search(rv.strip())
            if match:
                commit = match.group(1)
                uprint(DELETE_UNDO_MESSAGE.format(tag, commit))

        flash(self.view, TAG_DELETE_MESSAGE)
        util.view.refresh_gitsavvy(self.view)

    def delete_remote(self, interface):
        if not interface.remotes:
            return

        for remote_name, remote in interface.remotes.items():
            tags_to_delete = self.selected_remote_tags(remote_name)

            if tags_to_delete:
                self.git(
                    "push",
                    remote_name,
                    "--delete",
                    *("refs/tags/" + tag for tag in tags_to_delete)
                )

        flash(self.view, TAG_DELETE_MESSAGE)
        interface.remotes = None
        util.view.refresh_gitsavvy(self.view)


class gs_tags_push(TagsInterfaceCommand):

    """
    Displays a panel of all remotes defined for the repository, then push
    selected or all tag(s) to the selected remote.
    """

    def run(self, edit):
        show_remote_panel(self.push_selected, allow_direct=True)

    @on_worker
    def push_selected(self, remote):
        tags_to_push = self.selected_local_tags()

        flash(self.view, START_PUSH_MESSAGE)
        self.git("push", remote, *("refs/tags/" + tag for tag in tags_to_push))
        flash(self.view, END_PUSH_MESSAGE)

        interface = self.interface
        interface.state["remote_tags"] = {}
        util.view.refresh_gitsavvy(self.view)


class gs_tags_show_commit(TagsInterfaceCommand):

    """
    Display the commit for the selected tag's hash.
    """

    @on_worker
    def run(self, edit):
        interface = self.interface
        commit_hashes = self.selected_local_commits()

        for remote_name in interface.state["remotes"]:
            commit_hashes += self.selected_remote_commits(remote_name)

        for commit_hash in commit_hashes:
            self.window.run_command("gs_show_commit", {"commit_hash": commit_hash})


class gs_tags_show_graph(TagsInterfaceCommand):
    def run(self, edit) -> None:
        # NOTE: We take the tag name because the sha can be point to
        #       a real commit or a tag in case it's an annotated tag.
        #       Because in the graph a tag ref takes the form e.g.
        #       `tag: 2.14.5` we need to format it like that here.
        tags = self.selected_local_tags()
        if not tags:
            return
        if len(tags) > 1:
            flash(self.view, "Can only follow one tag. Taking the first one")

        self.window.run_command('gs_graph', {
            'all': True,
            'follow': "tag: {}".format(tags[0])
        })


class gs_tags_navigate_tag(GsNavigate):

    """
    Move cursor to the next (or previous) selectable file in the dashboard.
    """
    offset = 0

    def get_available_regions(self):
        return self.view.find_by_selector("constant.other.git-savvy.tags.sha1")
