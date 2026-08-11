"""Bounded scene-spec data model. Pure standard library; JSON round-trips."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Optional


EMPHASIS_TYPES = ("indicate", "circumscribe", "flash", "wiggle")
MAX_EQUATIONS = 32
MAX_EQUATION_BYTES = 4096
MAX_TOTAL_EQUATION_BYTES = 64 * 1024
MAX_EMPHASES = 32
MAX_PIXELS = 3840 * 2160
MAX_SCENE_SECONDS = 180.0
SCENE_KEYS = frozenset(
    {
        "equations",
        "title",
        "run_time",
        "hold",
        "width",
        "height",
        "fps",
        "background",
        "font",
        "scale",
        "emphases",
    }
)
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")
_DANGEROUS_TEX_RE = re.compile(
    r"\\(?:"
    r"@+input|input|include|includeonly|includegraphics|"
    r"openin|openout|read|write|newread|newwrite|immediate|special|"
    r"usepackage|documentclass|bibliography|addbibresource|"
    r"verbatiminput|lstinputlisting|"
    r"catcode|csname|endcsname|"
    r"def|edef|gdef|xdef|let|futurelet|newcommand|renewcommand|"
    r"loop|repeat|everyjob|everyeof|everypar|everymath|"
    r"directlua|latelua|luafunction|pdf[a-z]+|shellescape"
    r")(?![A-Za-z@])",
    re.IGNORECASE,
)
_DOCUMENT_BOUNDARY_RE = re.compile(
    r"\\(?:begin|end)\s*\{\s*document\s*\}", re.IGNORECASE
)


def _bounded_text(value: object, *, name: str, max_bytes: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if (
        not value
        or len(value.encode("utf-8")) > max_bytes
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{name} is empty, oversized, or contains controls")
    return value


def _number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return converted


def _integer(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


@dataclass
class Emphasis:
    """Emphasis applied after the equation at index ``at`` is shown."""

    at: int
    type: str = "indicate"

    def validate(self, n_equations: int) -> None:
        if isinstance(self.at, bool) or not isinstance(self.at, int):
            raise ValueError("emphasis.at must be an integer")
        if self.type not in EMPHASIS_TYPES:
            raise ValueError(
                f"unknown emphasis type {self.type!r}; valid: {EMPHASIS_TYPES}"
            )
        if not (0 <= self.at < n_equations):
            raise ValueError(
                f"emphasis.at {self.at} out of range for {n_equations} equations"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"at": self.at, "type": self.type}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Emphasis":
        if (
            not isinstance(value, dict)
            or "at" not in value
            or not set(value).issubset({"at", "type"})
        ):
            raise ValueError("emphasis has an invalid shape")
        at = _integer(value["at"], name="emphasis.at")
        emphasis_type = value.get("type", "indicate")
        if not isinstance(emphasis_type, str):
            raise ValueError("emphasis.type must be a string")
        return cls(at=at, type=emphasis_type)


@dataclass
class SceneSpec:
    """A bounded sequence of equations written and morphed by Manim."""

    equations: list[str]
    title: Optional[str] = None
    run_time: float = 1.5
    hold: float = 0.6
    width: int = 1920
    height: int = 1080
    fps: int = 30
    background: str = "#0f172a"
    font: str = "Noto Sans"
    scale: float = 1.6
    emphases: list[Emphasis] = field(default_factory=list)

    def validate(self) -> None:
        if not isinstance(self.equations, list) or not (
            1 <= len(self.equations) <= MAX_EQUATIONS
        ):
            raise ValueError("scene spec needs between 1 and 32 equations")
        total_equation_bytes = 0
        for index, equation in enumerate(self.equations):
            equation = _bounded_text(
                equation,
                name=f"equations[{index}]",
                max_bytes=MAX_EQUATION_BYTES,
            )
            total_equation_bytes += len(equation.encode("utf-8"))
            if (
                "^^" in equation
                or _DANGEROUS_TEX_RE.search(equation)
                or _DOCUMENT_BOUNDARY_RE.search(equation)
            ):
                raise ValueError(f"equations[{index}] contains unsafe TeX")
        if total_equation_bytes > MAX_TOTAL_EQUATION_BYTES:
            raise ValueError("scene equations exceed the aggregate size limit")

        if self.title is not None:
            _bounded_text(self.title, name="title", max_bytes=512)
        _bounded_text(self.background, name="background", max_bytes=16)
        if _COLOR_RE.fullmatch(self.background) is None:
            raise ValueError("background must be a fixed hexadecimal color")
        font = _bounded_text(self.font, name="font", max_bytes=128)
        if any(character in font for character in "/\\"):
            raise ValueError("font must be a family name, not a path")

        if isinstance(self.width, bool) or not isinstance(self.width, int):
            raise ValueError("width must be an integer")
        if isinstance(self.height, bool) or not isinstance(self.height, int):
            raise ValueError("height must be an integer")
        if isinstance(self.fps, bool) or not isinstance(self.fps, int):
            raise ValueError("fps must be an integer")
        if not (320 <= self.width <= 3840 and 240 <= self.height <= 2160):
            raise ValueError("width/height are outside the render bounds")
        if self.width * self.height > MAX_PIXELS:
            raise ValueError("render pixel count exceeds the limit")
        if not 1 <= self.fps <= 60:
            raise ValueError("fps is outside the render bounds")

        run_time = _number(self.run_time, name="run_time")
        hold = _number(self.hold, name="hold")
        scale = _number(self.scale, name="scale")
        if not 0.05 <= run_time <= 15.0:
            raise ValueError("run_time is outside the animation bounds")
        if not 0.0 <= hold <= 30.0:
            raise ValueError("hold is outside the animation bounds")
        if not 0.1 <= scale <= 10.0:
            raise ValueError("scale is outside the layout bounds")

        if not isinstance(self.emphases, list) or len(self.emphases) > MAX_EMPHASES:
            raise ValueError("too many emphases")
        for emphasis in self.emphases:
            if not isinstance(emphasis, Emphasis):
                raise ValueError("emphases must contain Emphasis values")
            emphasis.validate(len(self.equations))
        estimated_seconds = (
            run_time * (len(self.equations) + int(self.title is not None))
            + hold
            + len(self.emphases)
        )
        if estimated_seconds > MAX_SCENE_SECONDS:
            raise ValueError("scene duration exceeds the render bound")

    def to_dict(self) -> dict[str, Any]:
        return {
            "equations": list(self.equations),
            "title": self.title,
            "run_time": self.run_time,
            "hold": self.hold,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "background": self.background,
            "font": self.font,
            "scale": self.scale,
            "emphases": [emphasis.to_dict() for emphasis in self.emphases],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SceneSpec":
        if (
            not isinstance(value, dict)
            or "equations" not in value
            or not set(value).issubset(SCENE_KEYS)
        ):
            raise ValueError("scene spec has an invalid shape")
        equations = value["equations"]
        emphases = value.get("emphases", [])
        if not isinstance(equations, list) or not isinstance(emphases, list):
            raise ValueError("equations and emphases must be lists")
        spec = cls(
            equations=list(equations),
            title=value.get("title"),
            run_time=_number(value.get("run_time", 1.5), name="run_time"),
            hold=_number(value.get("hold", 0.6), name="hold"),
            width=_integer(value.get("width", 1920), name="width"),
            height=_integer(value.get("height", 1080), name="height"),
            fps=_integer(value.get("fps", 30), name="fps"),
            background=value.get("background", "#0f172a"),
            font=value.get("font", "Noto Sans"),
            scale=_number(value.get("scale", 1.6), name="scale"),
            emphases=[Emphasis.from_dict(item) for item in emphases],
        )
        spec.validate()
        return spec
