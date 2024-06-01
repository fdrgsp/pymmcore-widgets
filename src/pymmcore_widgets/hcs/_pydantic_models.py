import numpy as np
from pydantic_compat import validator
from useq import GridRowsColumns, Position, RandomPoints
from useq._base_model import UseqModel

from pymmcore_widgets.hcs._fov_widget import Center
from pymmcore_widgets.hcs._plate_model import (
    Plate,
)


class Well(UseqModel):
    """General class describing a well.

    Attributes
    ----------
    name : str
        The name of the well.
    row : int
        The row of the well.
    column : int
        The column of the well.
    """

    name: str
    row: int
    column: int


class CalibrationData(UseqModel):
    """Calibration data for the plate.

    Attributes
    ----------
    plate : Plate | None
        The plate to calibrate. By default, None.
    well_A1_center : tuple[float, float]
        The x and y stage coordinates of the center of well A1. By default, None.
    rotation_matrix : np.ndarray | None
        The rotation matrix that should be used to correct any plate rortation.
        By default, None.
    calibration_position_a1 : list[tuple[float, float]]
        The x and y stage positions used to calibrate the well A1. By default, None.
    calibration_position_an : list[tuple[float, float]]
        The x and y stage positions used to calibrate the well An. By default, None.
    """

    plate: Plate | None = None
    well_A1_center: tuple[float, float] | None = None
    rotation_matrix: np.ndarray | None = None
    calibration_positions_a1: list[tuple[float, float]] | None = None
    calibration_positions_an: list[tuple[float, float]] | None = None

    @validator("rotation_matrix", pre=True, always=True)
    def validate_rotation_matrix(cls, v):
        return np.array(eval(v)) if isinstance(v, str) else v

    def serialize_rotation_matrix(self):
        return None if self.rotation_matrix is None else self.rotation_matrix.tolist()

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {  # noqa: RUF012
            np.ndarray: lambda v: v.tolist() if v is not None else None
        }


class HCSData(UseqModel):
    """Store all the info needed to setup an HCS experiment.

    Attributes
    ----------
    plate : Plate
        The selected well plate. By default, None.
    wells : list[Well] | None
        The selected wells as Well object: Well(name, row, column). By default, None.
    mode : Center | RandomPoints | GridRowsColumns | None
        The mode used to select the FOVs. By default, None.
    calibration : CalibrationData | None
        The data necessary to calibrate the plate. By default, None.
    positions : list[Position] | None
        The list of FOVs as useq.Positions expressed in stage coordinates.
        By default, None.
    """

    plate: Plate | None = None
    wells: list[Well] | None = None
    mode: Center | RandomPoints | GridRowsColumns | None = None
    calibration: CalibrationData | None = None
    positions: list[Position] | None = None


# database = load_database(DEFAULT_PLATE_DB_PATH)
# plate = database["standard 96 wp"]
# wells = [Well(name="A1", row=1, column=1), Well(name="B2", row=2, column=2)]
# mode = GridRowsColumns(rows=2, columns=2)
# posotions = [Position(x=1, y=1, z=1), Position(x=2, y=2, z=2)]
# calibration = CalibrationData(
#     plate=plate,
#     well_A1_center=(1, 1),
#     rotation_matrix=np.array([[1, 2], [3, 4]]),
#     calibration_positions_a1=[(1, 1), (2, 2)],
# )

# hcs_data = HCSData(
#     plate=plate, wells=wells, mode=mode, positions=posotions, calibration=calibration
# )

# print(hcs_data.model_dump_json())
