"""The Surface Focus settings being edited."""

from __future__ import annotations

from typing import Literal

import useq
from pydantic import BaseModel, ConfigDict, FiniteFloat, ValidationError

METHODS = {"linear": "Local linear surface", "plane": "Fitted plane"}


class ZSurface(BaseModel):
    """The method and reference points of a [`useq.FocusSurface`][] being edited.

    Unlike the `FocusSurface`, it can be incomplete (e.g. fewer than 3 points), so a
    setup in progress is kept. Its JSON is a `FocusSurface`'s.
    """

    model_config = ConfigDict(frozen=True)

    method: Literal["linear", "plane"] = "linear"
    points: tuple[tuple[FiniteFloat, FiniteFloat, FiniteFloat], ...] = ()

    def focus_surface(self) -> useq.FocusSurface:
        """Return the surface; raise `ValidationError` if it cannot be built."""
        return useq.FocusSurface(method=self.method, points=self.points)

    def problem(self) -> str:
        """Return why the surface cannot be used, or `""` if it can."""
        try:
            self.focus_surface()
        except ValidationError as e:
            return _message(e)
        return ""


def _message(error: ValidationError) -> str:
    # not in `problem()`: a local there would keep the error's frames, and so the
    # caller, alive in a reference cycle
    first = error.errors()[0]
    return str(first.get("ctx", {}).get("error", first["msg"]))
