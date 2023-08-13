from dataclasses import dataclass
from pathlib import Path

PLATE_DB_PATH = Path(__file__).parent / "well_plate_database.json"


@dataclass(frozen=True)
class WellPlate:
    """General well plates class."""

    id: str
    circular: bool
    rows: int
    cols: int
    well_spacing_x: float
    well_spacing_y: float
    well_size_x: float
    well_size_y: float

    @property
    def well_count(self) -> int:
        """Return the number of wells in the plate."""
        return self.rows * self.cols

    @property
    def plate_size(self) -> tuple[float, float]:
        """Return the size of the plate in the x and y direction.

        Size is calculated from well edge to well edge (along x and y).
        """
        return (
            (self.well_size_x * self.cols) + (self.well_spacing_x * (self.cols - 1)),
            (self.well_size_y * self.rows) + (self.well_spacing_y * (self.rows - 1)),
        )


def load_database(database_path: Path | str) -> dict[str, WellPlate]:
    """Load the database of well plates contained in well_plate_database.json."""
    import json

    with open(Path(database_path)) as f:
        return {k["id"]: WellPlate(**k) for k in json.load(f)}
