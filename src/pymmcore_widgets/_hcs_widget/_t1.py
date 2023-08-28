# import numpy as np
# import matplotlib.pyplot as plt

# angle_deg = 60

# r = 6.4
# h = 9

# cols = 12
# h *= cols - 1

# def _new_xy(angle_deg, h):
#     space_x = h * np.sin(np.deg2rad(angle_deg))
#     space_y = h * np.cos(np.deg2rad(angle_deg))
#     return space_x, space_y

# space_x, space_y = _new_xy(angle_deg, h)
# print(space_x, space_y)

# print(space_x - r, space_y)
# print(space_x, space_y + r)
# print(space_x + r, space_y)

# plt.plot(0, 0, "ko")
# plt.plot(space_x, space_y, "ro")
# plt.plot(space_x - r, space_y, "go")
# plt.plot(space_x, space_y + r, "go")
# plt.plot(space_x + r, space_y, "go")
# plt.axis("equal")
# plt.gca().invert_yaxis()
# plt.show()
