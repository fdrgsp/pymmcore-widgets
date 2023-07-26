from pathlib import Path

from pydantic import BaseModel


class FrozenModel(BaseModel):
    class Config:
        allow_population_by_field_name = True
        extra = "allow"
        frozen = True


class WellPlate(FrozenModel):
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


def _load_database() -> dict[str, WellPlate]:
    """Load the database of well plates contained in well_plate_database.json."""
    import json

    with open(Path(__file__).parent / "well_plate_database.json") as f:
        return {k["id"]: WellPlate(**k) for k in json.load(f)}


PLATE_DB = _load_database()
