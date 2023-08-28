# import matplotlib.pyplot as plt
# from matplotlib.patches import Circle, Rectangle
# from rich import print
# from useq import RandomArea, RandomPoints


# circle = True
# pixel = False


# if pixel:
#     well_px = RandomPoints(
#         circular=circle,
#         nFOV=3,
#         area=RandomArea(x=10, y=10, width=280, height=280),
#         random_seed=1,
#     )
#     well_px_1 = RandomPoints(
#         circular=circle,
#         nFOV=3,
#         area=RandomArea(x=10, y=10, width=280, height=280),
#         random_seed=1,
#     )
#     print(list(well_px))

# else:
#     well_mm = RandomPoints(
#         circular=circle,
#         nFOV=3,
#         area=RandomArea(x=0.0, y=0.0, width=18.0, height=18.0),
#         random_seed=1,
#     )

#     well_mm_1 = RandomPoints(
#         circular=circle,
#         nFOV=3,
#         area=RandomArea(x=4.5, y=4.5, width=9.0, height=9.0),
#         random_seed=1,
#     )
#     print(list(well_mm))
#     print(list(well_mm_1))

# fig, ax = plt.subplots()

# if circle:
#     if pixel:
#         ax.add_patch(
#             Circle(
#                 (well_px.area.center.x, well_px.area.center.y),
#                 well_px.area.width / 2,
#                 fill=False,
#                 color="m",
#             )
#         )
#     else:
#         ax.add_patch(
#             Circle(
#                 (well_mm.area.center.x, well_mm.area.center.y),
#                 well_mm.area.width / 2,
#                 fill=False,
#                 color="g",
#             )
#         )
#         ax.add_patch(
#             Circle(
#                 (well_mm_1.area.center.x, well_mm_1.area.center.y),
#                 well_mm_1.area.width / 2,
#                 fill=False,
#                 color="m",
#             )
#         )
# else:
#     if pixel:
#         ax.add_patch(
#             Rectangle(
#                 (well_px.area.x, well_px.area.y),
#                 well_px.area.width,
#                 well_px.area.height,
#                 fill=False,
#                 color="k",
#             )
#         )
#     else:
#         ax.add_patch(
#             Rectangle(
#                 (well_mm.area.x, well_mm.area.y),
#                 well_mm.area.width,
#                 well_mm.area.height,
#                 fill=False,
#                 color="g",
#             )
#         )
#         ax.add_patch(
#             Rectangle(
#                 (well_mm_1.area.x, well_mm_1.area.y),
#                 well_mm_1.area.width,
#                 well_mm_1.area.height,
#                 fill=False,
#                 color="m",
#             )
#         )

# if pixel:
#     for x, y in well_px:
#         ax.plot(x, y, "ko")

# else:
#     for x, y in well_mm:
#         ax.plot(x, y, "go")

#     for x, y in well_mm_1:
#         ax.plot(x, y, "mo")


# plt.axis("equal")
# plt.gca().invert_yaxis()
# plt.show()


# grid_mm = GridRelative(
#     overlap=(0.0, 0.0),
#     mode="row_wise_snake",
#     fov_width=0.512,
#     fov_height=0.512,
#     rows=2,
#     columns=2,
#     relative_to="center",
# )
# # grid_px = GridRelative(
# #     overlap=(0.0, 0.0),
# #     mode="row_wise_snake",
# #     fov_width=8.533333333333333,
# #     fov_height=8.533333333333333,
# #     rows=2,
# #     columns=2,
# #     relative_to='center'
# # )


# print(list(grid_mm))
# # print(list(grid_px))

# fig, ax = plt.subplots()

# for g in grid_mm:
#     plt.plot(g.x, g.y, "go")
#     ax.add_patch(
#         Rectangle(
#             (g.x - (grid_mm.fov_width / 2), g.y - (grid_mm.fov_height / 2)),
#             grid_mm.fov_width,
#             grid_mm.fov_height,
#             fill=False,
#             color="g",
#         )
#     )

# # for g in grid_px:
# #     plt.plot(g.x, g.y, "mo")

# plt.axis("equal")
# plt.show()


# def shift_points(center, points, new_center):
#     shift_x = new_center[0] - center[0]
#     shift_y = new_center[1] - center[1]

#     shifted_points = [(point[0] + shift_x, point[1] + shift_y) for point in points]

#     return shifted_points

# # Example usage
# center = (9, 9)
# points = [(5, 5), (10, 11), (9, 12)]
# new_center = (12, 12)

# shifted_points = shift_points(center, points, new_center)
# print(shifted_points)


# plt.plot(center[0], center[1], "mo")
# plt.plot(new_center[0], new_center[1], "ko")
# for x, y in points:
#     plt.plot(x, y, "ro")
# for x, y in shifted_points:
#     plt.plot(x, y, "go")

# plt.axis("equal")
# plt.show()
