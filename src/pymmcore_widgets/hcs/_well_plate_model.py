from dataclasses import dataclass
from pathlib import Path

PLATE_DB_PATH = Path(__file__).parent / "well_plate_database.json"


@dataclass(frozen=True)
class WellPlate:
    """General well plates class."""

    id: str = ""
    circular: bool = False
    rows: int = 0
    columns: int = 0
    well_spacing_x: float = 0.0
    well_spacing_y: float = 0.0
    well_size_x: float = 0.0
    well_size_y: float = 0.0


def load_database(database_path: Path | str) -> dict[str, WellPlate]:
    """Load the database of well plates contained in database_path.

    The database must be a JSON file.
    """
    import json

    with open(Path(database_path)) as f:
        return {k["id"]: WellPlate(**k) for k in json.load(f)}
