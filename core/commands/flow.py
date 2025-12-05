import sublime

from ..ui_mixins.input_panel import show_single_line_input_panel
from ..ui__quick_panel import show_noop_panel
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.runtime import on_new_thread


from typing import List


INIT_REQUIRED_MSG = "Please run `git: flow init` first."

GITFLOW_CONF = ['branch.master', 'branch.develop', 'prefix.feature',
                'prefix.release', 'prefix.hotfix', 'prefix.versiontag',
                'prefix.support', 'origin', ]


class FlowMixin(GsWindowCommand):
    """
    Common git-flow commands parent class.

    Populates gitflow settings and includes useful methods
    for option selection and branch retrieval.
    """
    def run(self, **kwargs):
        self.get_flow_settings()
        if not self.flow_settings['branch.master']:
            show_noop_panel(self.window, INIT_REQUIRED_MSG)

    def is_visible(self, **kwargs):
        return self.savvy_settings.get("show_git_flow_commands") or False

    def get_flow_settings(self):
        flow_ver = self.git("flow", "version")
        self.flow_settings = {
            'flow.version': flow_ver,
        }
        for conf in GITFLOW_CONF:
            self.flow_settings[conf] = self.git(
                "config", "gitflow.%s" % conf, throw_on_error=False
            ).strip()

    def _generic_select(self, help_text, options, callback,
                        no_opts="There are no branches available"):
        """
        Display quick_panel with help_text as first option and options as
        the rest and passes given callback to `show_quick_panel`.

        In case options is empty or None displays only `no_opts` text.
        """
        if not options:
            show_noop_panel(self.window, no_opts)
        else:
            self.window.show_quick_panel(
                [help_text] + options,
                callback,
                flags=sublime.MONOSPACE_FONT
            )

    def get_value(self, options, index):
        """
        Convert a selected quick_panel index to selected option.
        Ignores first option (which is the query).
        """
        # If the user pressed `esc` or otherwise cancelled.
        if index == -1 or index == 0:
            return None
        selected = options[index - 1]  # skipping help query
        if selected.startswith('* '):
            selected = selected[2:]
        return selected


class GsGitFlowInitCommand(FlowMixin):
    branches = []  # type: List[str]
    """
    Through a series of panels, allow the user to initialize git-flow.
    """

    def configure_gitflow(self, conf, value):
        self.git("config", "gitflow.%s" % conf, value)

        if conf.startswith('branch'):
            # remove this branch from branches available to next command
            self.branches = [b for b in self.branches if b != value]

    def run(self, reinit=False, **kwargs):
        self.get_flow_settings()
        if self.flow_settings['branch.master'] and not reinit:
            def confirm_reinit(index):
                if index == 1:  # Yes
                    return self.run(reinit=True)
            self._generic_select('Git flow is already initialized, re-init?',
                                 ['Yes', 'No'], confirm_reinit)
            return

        self.remotes = list(self.get_remotes().keys())
        self._generic_select('Remote to use as origin in git flow?',
                             self.remotes, self.on_origin_selected,
                             no_opts="There are no remotes available.")

    def on_origin_selected(self, index):
        value = self.get_value(self.remotes, index)
        if not value:
            return
        self.configure_gitflow('origin', value)

        self.branches = [b.name for b in self.get_local_branches()]

        self._generic_select('Branch for production releases (master)',
                             self.branches, self.on_master_selected)

    def on_master_selected(self, index):
        value = self.get_value(self.branches, index)
        if not value:
            return
        self.configure_gitflow('branch.master', value)

        if not self.branches:
            self._generic_select('No branches found, create branch "develop"?',
                                 ['Yes', 'No'], self.create_develop_branch)
        self._generic_select('Branch for "next release" development',
                             self.branches, self.on_develop_selected)

    def create_develop_branch(self, index):
        if index == 1:
            self.git('branch', 'develop')
            self.branches = [b.name for b in self.get_local_branches()]
            self.on_develop_selected(1)

    def on_develop_selected(self, index):
        value = self.get_value(self.branches, index)
        if not value:
            return

        # TODO: create develop branch if does not exist yet
        self.configure_gitflow('branch.develop', value)

        show_single_line_input_panel("Feature branches prefix?: ", "feature/",
                                     self.on_feature_selected)

    def on_feature_selected(self, value):
        self.configure_gitflow('prefix.feature', value)

        show_single_line_input_panel("Release branches prefix?: ", "release/",
                                     self.on_release_selectes)

    def on_release_selectes(self, value):
        self.configure_gitflow('prefix.release', value)
        show_single_line_input_panel("Hotfix branches prefix?: ", "hotfix/",
                                     self.on_hotfix_selected)

    def on_hotfix_selected(self, value):
        self.configure_gitflow('prefix.hotfix', value)
        show_single_line_input_panel("Support branches prefix?: ", "support/",
                                     self.on_support_selected)

    def on_support_selected(self, value):
        self.configure_gitflow('prefix.support', value)
        show_single_line_input_panel("Version tag prefix?: ", " ",
                                     self.on_versiontag_selected)

    def on_versiontag_selected(self, tag):
        self.configure_gitflow('prefix.versiontag', tag)
        self.window.status_message("git flow initialized")


class CompleteMixin(FlowMixin):
    """
    These are the final methods called after setup, which call the actual
    git-flow command and display a `status_message` update.
    """
    command = None  # type: str
    flow = None  # type: str
    prefix_setting = None  # type: str
    query = None  # type: str

    @on_new_thread
    def complete_flow(self, name=None):
        self.git("flow", self.flow, self.command, name)
        self.show_status_update()

    def show_status_update(self):
        self.window.status_message(
            "%s %sed, checked out %s" %
            (self.flow.capitalize(), self.command,
                self.get_current_branch_name()))


class GenericStartMixin(CompleteMixin):
    """
    A common `run` method for flow X "start" commands.
    """
    command = 'start'

    def run(self, **kwargs):
        super(GenericStartMixin, self).run(**kwargs)
        self.prefix = self.flow_settings[self.prefix_setting]
        show_single_line_input_panel(self.query, "", self.complete_flow)


class GenericSelectTargetBranch(CompleteMixin):
    """
    A useful helper class to prompt for confirmation (if on a branch
    belonging to flow) or prompt to select a branch if not.
    """
    name_prompt = None  # type: str

    def run(self, name=None, **kwargs):
        super(GenericSelectTargetBranch, self).run(**kwargs)
        self.prefix = self.flow_settings[self.prefix_setting]
        curbranch = self.get_current_branch_name()

        if name is None:
            if curbranch and curbranch.startswith(self.prefix):
                self.cur_name = name = curbranch.replace(self.prefix, '')
            else:
                self.branches = [
                    b.name.replace(self.prefix, '')
                    for b in self.get_local_branches()
                    if b.name.startswith(self.prefix)
                ]
                self._generic_select(
                    self.name_prompt,
                    self.branches,
                    self.on_name_selected,
                )
                return

        self._generic_select(self.query % name, ['Yes', 'No'],
                             self.on_select_current)

    def on_select_current(self, index):
        if index != 1:
            return None
        self.complete_flow(name=self.cur_name)

    def on_name_selected(self, index):
        value = self.get_value(self.branches, index)
        if not value:
            return
        self.complete_flow(name=value)


class GenericFinishMixin(GenericSelectTargetBranch):
    command = 'finish'


class GenericPublishMixin(GenericSelectTargetBranch):
    command = 'publish'

    def show_status_update(self):
        self.window.status_message(
            "%s %sed" %
            (self.flow.capitalize(),
                self.command))


class GenericTrackCommand(CompleteMixin):
    """
    Common mixin to prompt for branch to track and call `complete_flow`.
    """
    command = 'track'

    def run(self, name=None, **kwargs):
        super(GenericTrackCommand, self).run(**kwargs)
        if name:
            self.complete_flow(name)
        else:
            show_single_line_input_panel(self.query, "", self.complete_flow)


class GsGitFlowFeatureStartCommand(GenericStartMixin):
    prefix_setting = 'prefix.feature'
    query = "Feature name?: "
    flow = "feature"


class GsGitFlowFeatureFinishCommand(GenericFinishMixin):
    prefix_setting = 'prefix.feature'
    query = 'Finish feature: %s?'
    name_prompt = 'Finish which feature?'
    flow = "feature"


class GsGitFlowFeaturePublishCommand(GenericPublishMixin):
    prefix_setting = 'prefix.feature'
    query = 'Publish feature: %s?'
    name_prompt = 'Publish which feature?'
    flow = "feature"


class GsGitFlowFeatureTrackCommand(GenericTrackCommand):
    query = 'Track which feature?:'
    flow = "feature"


class GsGitFlowFeaturePullCommand(CompleteMixin):
    """
    This command first prompts for a remote name and then a feature to pull,
    before completing the flow.
    """
    prefix_setting = 'prefix.feature'
    query = 'Pull which feature?:'
    flow = "feature"
    command = 'pull'

    def run(self, name=None, **kwargs):
        super(GsGitFlowFeaturePullCommand, self).run(**kwargs)
        self.remotes = list(self.get_remotes().keys())
        self._generic_select('Remote to pull feature from?',
                             self.remotes, self.on_remote_selected,
                             no_opts="There are no remotes available.")

    def on_remote_selected(self, index):
        value = self.get_value(self.remotes, index)
        if not value:
            return
        self.remote = value
        show_single_line_input_panel(self.query, "", self.complete_flow)

    @on_new_thread
    def complete_flow(self, name=None):
        self.git("flow", self.flow, "pull", self.remote, name)
        self.show_status_update()


class GsGitFlowReleaseStartCommand(GenericStartMixin):
    prefix_setting = 'prefix.release'
    query = "Release version?: "
    flow = "release"


class GsGitFlowReleaseFinishCommand(GenericFinishMixin):
    prefix_setting = 'prefix.release'
    query = 'Finish release: %s?'
    name_prompt = 'Finish which release?'
    flow = "release"


class GsGitFlowReleasePublishCommand(GenericPublishMixin):
    prefix_setting = 'prefix.release'
    query = 'Publish release: %s?'
    name_prompt = 'Publish which release?'
    flow = "release"


class GsGitFlowReleaseTrackCommand(GenericTrackCommand):
    query = 'Track which release?:'
    flow = "release"


class GsGitFlowHotfixStartCommand(GenericStartMixin):
    prefix_setting = 'prefix.hotfix'
    query = "Hotfix name?: "
    flow = "hotfix"


class GsGitFlowHotfixFinishCommand(GenericFinishMixin):
    prefix_setting = 'prefix.hotfix'
    query = 'Finish hotfix: %s?'
    name_prompt = 'Finish which hotfix?'
    flow = "hotfix"


class GsGitFlowHotfixPublishCommand(GenericPublishMixin):
    prefix_setting = 'prefix.hotfix'
    query = 'Publish hotfix: %s?'
    name_prompt = 'Publish which hotfix?'
    flow = "hotfix"


class GsGitFlowSupportStartCommand(GenericStartMixin):
    prefix_setting = 'prefix.support'
    query = "Support name?: "
    flow = "support"
