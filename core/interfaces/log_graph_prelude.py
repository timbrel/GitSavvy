from __future__ import annotations

from dataclasses import dataclass

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

    def to_dict(self) -> dict:
        return {
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
            ]
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
