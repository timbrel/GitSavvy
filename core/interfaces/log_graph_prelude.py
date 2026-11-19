from __future__ import annotations

from dataclasses import dataclass
import re

import sublime


@dataclass(frozen=True)
class Prelude:
    """Text and controls rendered together, relative to buffer position zero."""
    text: str
    controls: PreludeControls


@dataclass(frozen=True)
class PreludeControls:
    all: Toggle | None
    refs: list[RefToggle]
    resolve_base: sublime.Region | None = None

    def to_dict(self) -> dict:
        return {
            **({"resolve_base": [self.resolve_base.a, self.resolve_base.b]} if self.resolve_base else {}),
            "all": {
                "region": [self.all.region.a, self.all.region.b],
                "selected": self.all.selected
            } if self.all is not None else None,
            "refs": [
                {
                    "ref": toggle.ref,
                    "region": [toggle.region.a, toggle.region.b],
                    "selected": toggle.selected
                }
                for toggle in self.refs
            ]
        }

    @classmethod
    def from_dict(cls, data: dict) -> PreludeControls:
        all_toggle = data.get("all")
        return cls(
            Toggle(sublime.Region(*all_toggle["region"]), all_toggle["selected"])
            if all_toggle is not None else None,
            [
                RefToggle(sublime.Region(*item["region"]), item["selected"], item["ref"])
                for item in data.get("refs", [])
            ],
            sublime.Region(*data["resolve_base"]) if "resolve_base" in data else None
        )


@dataclass(frozen=True)
class Toggle:
    region: sublime.Region
    selected: bool

    def contains(self, point: int) -> bool:
        return self.region.begin() <= point < self.region.end()


@dataclass(frozen=True)
class RefToggle(Toggle):
    ref: str


def short_ref(ref: str) -> str:
    return re.sub(r"^refs/(?:heads|remotes|tags)/", "", ref)


def comparison_tips(choices: list[str]) -> list[str]:
    if any(ref.endswith("^!") for ref in choices):
        return []  # The resolved cutoff takes the place of [!].
    tips = [ref for ref in choices if not (ref.startswith(("-", "^")) or ".." in ref or ref.endswith("^@"))]
    return tips if len(tips) >= 2 else []
