from __future__ import annotations
from ...common import util
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.runtime import on_worker


__all__ = (
    "gs_bisect_start",
    "gs_bisect_good",
    "gs_bisect_bad",
    "gs_bisect_skip",
    "gs_bisect_reset",
)


class gs_bisect_start(GsWindowCommand):
    @on_worker
    def run(self, bad: str = "HEAD", good: list[str] = []):
        try:
            self.git("bisect", "start", bad, *good, show_panel=True)
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_bisect_good(GsWindowCommand):
    @on_worker
    def run(self, commit: str | None = None):
        try:
            if not self.in_bisect():
                self.git("bisect", "start")
            self.git("bisect", "good", commit, show_panel=True)
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_bisect_bad(GsWindowCommand):
    @on_worker
    def run(self, commit: str | None = None):
        try:
            if not self.in_bisect():
                self.git("bisect", "start")
            self.git("bisect", "bad", commit, show_panel=True)
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_bisect_skip(GsWindowCommand):
    @on_worker
    def run(self, commit: str | None = None):
        try:
            self.git("bisect", "skip", commit, show_panel=True)
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_bisect_reset(GsWindowCommand):
    @on_worker
    def run(self, commit: str | None = None):
        try:
            self.git("bisect", "reset", commit)
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)
