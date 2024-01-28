from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class BaseDataclass:
    """Base class for dataclasses."""

    # using "BaseDataclass" instead of give some type errors
    def replace(self, **kwargs: Any) -> Any:
        """Return a new plate with the given attributes replaced."""
        return self.__class__(**{**asdict(self), **kwargs})

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary representation of the BaseDataclass."""
        return asdict(self)
