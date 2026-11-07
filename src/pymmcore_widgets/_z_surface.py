"""The Surface Focus settings being edited."""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING, Literal, NamedTuple

import useq
from pydantic import BaseModel, ConfigDict, FiniteFloat, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable

METHODS = {"linear": "Local linear surface", "plane": "Fitted plane"}


class SurfaceStage(NamedTuple):
    """The stage, to set reference points from and to test the surface with."""

    position: Callable[[], tuple[float, float, float]]  # the current XYZ
    move_xy: Callable[[float, float], object]
    move_z: Callable[[float], object]


class ZSurface(BaseModel):
    """The method and reference points of a [`useq.FocusSurface`][] being edited.

    Unlike the `FocusSurface`, it can be incomplete (e.g. fewer than 3 points), so a
    setup in progress is kept. Its JSON is a `FocusSurface`'s.
    """

    model_config = ConfigDict(frozen=True)

    method: Literal["linear", "plane"] = "linear"
    points: tuple[tuple[FiniteFloat, FiniteFloat, FiniteFloat], ...] = ()

    @cached_property
    def _built(self) -> useq.FocusSurface | str:
        """The surface, or why it cannot be built."""
        try:
            return useq.FocusSurface(method=self.method, points=self.points)
        except ValidationError as e:
            return _message(e)

    def focus_surface(self) -> useq.FocusSurface:
        """Return the surface; raise `ValueError` if it cannot be built."""
        if isinstance(built := self._built, str):
            raise ValueError(built)
        return built

    def problem(self) -> str:
        """Return why the surface cannot be used, or `""` if it can."""
        return built if isinstance(built := self._built, str) else ""


def _message(error: ValidationError) -> str:
    # not in `_built`: a local there would keep the error's frames, and so the
    # caller, alive in a reference cycle
    first = error.errors()[0]
    return str(first.get("ctx", {}).get("error", first["msg"]))
