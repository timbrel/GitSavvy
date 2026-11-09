from unittest import TestCase

import sublime_plugin

from GitSavvy import git_savvy


class TestCommandExports(TestCase):
    def test_exposed_commands_use_their_lowercase_names(self):
        commands = {
            name: value
            for name, value in vars(git_savvy).items()
            if isinstance(value, type)
            and value.__module__.startswith("GitSavvy.")
            and issubclass(value, (
                sublime_plugin.TextCommand,
                sublime_plugin.WindowCommand,
                sublime_plugin.ApplicationCommand,
            ))
        }
        self.assertTrue(commands)
        for name, command in commands.items():
            with self.subTest(name=name):
                self.assertEqual(name, command.__name__)
                self.assertEqual(name, name.lower())

    def test_renamed_commands_remain_exposed(self):
        names = (
            "gs_help", "gs_help_browse", "gs_help_goto_link", "gs_help_goto_previous",
            "gs_generate_change_log", "gs_fixup_from_stage",
            "gs_quick_stage_current_file_and_fixup", "gs_mv_current_file",
            "gs_stage_diff", "gs_stage_diff_apply", "gs_stash", "gs_stash_apply",
            "gs_stash_pop", "gs_stash_drop", "gs_stash_show", "gs_stash_save",
            "gs_git_flow_init", "gs_git_flow_feature_start", "gs_git_flow_feature_finish",
            "gs_git_flow_feature_publish", "gs_git_flow_feature_track", "gs_git_flow_feature_pull",
            "gs_git_flow_release_start", "gs_git_flow_release_finish",
            "gs_git_flow_release_publish", "gs_git_flow_release_track",
            "gs_git_flow_hotfix_start", "gs_git_flow_hotfix_finish",
            "gs_git_flow_hotfix_publish", "gs_git_flow_support_start",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(getattr(git_savvy, name).__name__, name)

    def test_stash_listener_remains_exposed(self):
        self.assertTrue(issubclass(git_savvy.RevalidateStashView, sublime_plugin.EventListener))
