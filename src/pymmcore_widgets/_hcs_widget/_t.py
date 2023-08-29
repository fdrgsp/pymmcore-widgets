import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle
from rich import print
from useq import GridRelative, RandomPoints  # type: ignore

circle = True
pixel = False


if pixel:
    well_px = RandomPoints(
        circular=circle,
        nFOV=3,
        area_width=100,
        area_height=100,
        random_seed=1,
    )
    print(list(well_px))

else:
    well_mm = RandomPoints(
        circular=circle,
        nFOV=3,
        area_width=6.4,
        area_height=6.4,
        random_seed=20,
    )
    print(list(well_mm))

fig, ax = plt.subplots()

if circle:
    if pixel:
        ax.add_patch(
            Circle(
                (0, 0),
                well_px.area.width / 2,
                fill=False,
                color="m",
            )
        )
    else:
        ax.add_patch(
            Circle(
                (0, 0),
                well_mm.area_width / 2,
                fill=False,
                color="g",
            )
        )
else:
    if pixel:
        ax.add_patch(
            Rectangle(
                (-well_px.area_width / 2, -well_px.area_height / 2),
                well_px.area_width,
                well_px.area_height,
                fill=False,
                color="k",
            )
        )
    else:
        ax.add_patch(
            Rectangle(
                (-well_mm.area_width / 2, -well_mm.area_height / 2),
                well_mm.area_width,
                well_mm.area_height,
                fill=False,
                color="g",
            )
        )
        # ax.add_patch(
        #     Rectangle(
        #         (well_mm_1.area.x, well_mm_1.area.y),
        #         well_mm_1.area.width,
        #         well_mm_1.area.height,
        #         fill=False,
        #         color="m",
        #     )
        # )

if pixel:
    for x, y in well_px:
        ax.plot(x, y, "ko")

else:
    for x, y in well_mm:
        ax.plot(x, y, "go")

    # for x, y in well_mm_1:
    #     ax.plot(x, y, "mo")


plt.axis("equal")
plt.show()


grid_mm = GridRelative(
    overlap=(0.0, 0.0),
    mode="row_wise_snake",
    fov_width=0.512,
    fov_height=0.512,
    rows=2,
    columns=2,
    relative_to="center",
)
# grid_px = GridRelative(
#     overlap=(0.0, 0.0),
#     mode="row_wise_snake",
#     fov_width=8.533333333333333,
#     fov_height=8.533333333333333,
#     rows=2,
#     columns=2,
#     relative_to='center'
# )


print(list(grid_mm))
# print(list(grid_px))

fig, ax = plt.subplots()

for idx, g in enumerate(grid_mm):
    cl = "yo" if idx == 0 else "go"
    plt.plot(g.x, g.y, cl)  # type: ignore
    ax.add_patch(
        Rectangle(
            (g.x - (grid_mm.fov_width / 2), g.y - (grid_mm.fov_height / 2)),  # type: ignore  # noqa: E501
            grid_mm.fov_width,  # type: ignore
            grid_mm.fov_height,  # type: ignore
            fill=False,
            color="g",
        )
    )

# for g in grid_px:
#     plt.plot(g.x, g.y, "mo")

plt.axis("equal")
plt.show()
