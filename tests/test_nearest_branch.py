from textwrap import dedent

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import when
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.git_mixins.rebase import NearestBranchMixin


examples = [
    (
        "single branch",
        dedent("""\
        [master] 0.22.2
        """.rstrip()),
        "master",
        "master"
    ),
    (
        "normal commit",
        dedent("""\
        ! [alt-buffering] Use stdout buffer size as chunk size
         ! [better-follow-line] Abort "open file on remote" for unpushed revisions
          ! [better-general-navigate] Do not render dashboards twice on resurrect
           ! [dev] Merge pull request #1270 from timbrel/fixes
            * [fix-1261] Simplify `nearest_branch`
             ! [fixes] Do not offer "reset" on the head commit
              ! [follow-path-upwards] Stash
               ! [go-branches] Filter state by `repo_path` in a private method
                ! [inline-diff-stage-toggle] Allow toggling between staged/unstaged via `<TAB>`
                 ! [master] Merge branch 'dev'
                  ! [status-bar-updater] Remove `debug.disable_logging`
                   ! [throttle-status-bar-updates] Rewrite throttler for the status bar
        ------------
            *        [fix-1261] Simplify `nearest_branch`
         +           [better-follow-line] Abort "open file on remote" for unpushed revisions
         + +*+       [fixes] Do not offer "reset" on the head commit
        """.rstrip()),
        "fix-1261",
        "fixes"
    ),
    (
        "merge commit",
        dedent("""\
        ! [alt-buffering] Use stdout buffer size as chunk size
         ! [better-follow-line] Abort "open file on remote" for unpushed revisions
          ! [better-general-navigate] Do not render dashboards twice on resurrect
           ! [dev] Merge pull request #1270 from timbrel/fixes
            * [fix-1261] Simplify `nearest_branch`
             ! [fixes] Do not offer "reset" on the head commit
              ! [follow-path-upwards] Stash
               ! [go-branches] Filter state by `repo_path` in a private method
                ! [inline-diff-stage-toggle] Allow toggling between staged/unstaged via `<TAB>`
                 ! [master] Merge branch 'dev'
                  ! [status-bar-updater] Remove `debug.disable_logging`
                   ! [throttle-status-bar-updates] Rewrite throttler for the status bar
        ------------
            *        [fix-1261] Simplify `nearest_branch`
         +           [better-follow-line] Abort "open file on remote" for unpushed revisions
         - --        [dev] Merge pull request #1270 from timbrel/fixes
        """.rstrip()),
        "fix-1261",
        "dev"
    ),
    (
        "regex does not catch [x] pattern within the commit message",
        dedent("""\
        ! [alt-buffering] Use stdout buffer size as chunk size
         ! [better-follow-line] Abort "open file on remote" for unpushed revisions
          ! [better-general-navigate] Do not render dashboards twice on resurrect
           ! [dev] Merge pull request #1270 from timbrel/fixes
            * [fix-1261] Simplify `nearest_branch`
             ! [fixes] Do not offer "reset" on the head commit
              ! [follow-path-upwards] Stash
               ! [go-branches] Filter state by `repo_path` in a private method
                ! [inline-diff-stage-toggle] Allow toggling between staged/unstaged via `<TAB>`
                 ! [master] Merge branch 'dev'
                  ! [status-bar-updater] Remove `debug.disable_logging`
                   ! [throttle-status-bar-updates] Rewrite throttler for the status bar
        ------------
            *        [fix-1261] Simplify `nearest_branch`
         +           [better-follow-line] Abort "open file on remote" for unpushed revisions
         + +*+       [fixes] Do not `[offer]` "reset" on the head commit
        """.rstrip()),
        "fix-1261",
        "fixes"
    )
]


class TestNearestBranch(DeferrableTestCase):
    @p.expand(examples)
    def test_a(self, _, show_branch_output, active_branch, nearest_branch):
        test = NearestBranchMixin()
        when(test, strict=False).git("show-branch", "--no-color").thenReturn(show_branch_output)
        self.assertEqual(nearest_branch, test.nearest_branch(active_branch))
