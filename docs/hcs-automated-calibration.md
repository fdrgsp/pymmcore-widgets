# Automated HCS plate calibration (experimental)

The calibration page has **Manual** and **Automated** tabs with the same layout.
Automated has a two-corner table on the left and three tabs on the right:
**Select Wells**, **Calibrate Plate**, and **Test Calibration**. A configured
pixel size is required.
A single square or rectangular well (e.g. a coverslip) shows only Manual.
Both methods use compact point tables sized to fit their rows.

Install the optional detector dependencies:

```sh
uv sync --extra hcs-auto
```

1. Select a plate, then open **Automated**. Set a
   low-magnification objective, brightfield illumination, and focus on the rims.
   Stop live view before scanning.
2. In **Select Wells**, select every well whose complete rim will be included in
   the scan area. A **2 x 2 block is recommended**, such as B2/B3/C2/C3.
   Alternatively, select **three or more wells in a single row or column**;
   single-row and single-column strips are supported. Other non-collinear groups
   of three or more wells also work. Click wells or drag a selection rectangle.
3. Use **Set** in the left table to record two opposing stage corners enclosing
   those wells. The green tick indicates a valid area. **Clear** and **Clear All**
   work as they do in Manual. Start Scan and Calibrate Plate enable only after
   both corners and a valid well selection are provided. A diagonal line alone
   is insufficient.
4. Select **Start Scan**. The widget switches to **Calibrate Plate** and shows
   each acquired image in the VisPy canvas. Spacing and placement use the
   configured pixel size. Cancel stops between operations and retains the last
   applied calibration. Detection does not run automatically.
5. Open the gear dialog to set edge smoothing, detection threshold, and minimum
   rim coverage, then click **Detect Wells**. Detection can be repeated on the
   retained images without another scan. Settings use aligned label-and-spin rows.
6. The selected plate wells supply the labels, and the measured grid supplies
   rotation. There is no Identify Wells button or manual rotation input. The
   usual mounting orientation is assumed (columns roughly along positive stage
   X). The detected count and arrangement must match the selection; otherwise
   adjust the settings or selection. All detected rims remain visible for review.
7. Use **Show detection** to toggle the overlay. When the result matches the
   selection and passes calibration checks, choose **Apply Calibration**.
8. Open **Test Calibration** and double-click points to test the result. The
   applied plan is shared with Manual and saves in the existing `WellPlatePlan`
   JSON format.

Changing selected wells retains the scan images but clears the detection draft,
so you can run detection again against the corrected selection. Changing the
corners or plate model clears incompatible scan images. A cancelled or rejected
result does not replace an already applied calibration.

The A1 center and rotation are a least-squares fit of the well centers at the
plate model's nominal spacing, which is the spacing the exported plan uses. The
spacing is still measured (only along the line for a row or column) and shown,
with a warning when it differs from the plate model by more than 5%. The
maximum deviation is the largest distance between a measured center and its
plan position; above 100 µm the status line warns, but Apply stays enabled. A
2 x 2 block measures both plate directions and is preferred when available.

Circular, elliptical, square, and rectangular wells are detected; the detector
is chosen from the plate model, so no selector is shown. Plates with fewer than
three wells need every well: a two-well plate is calibrated along its row or
column, and a single round or elliptical well fixes only the A1 center (no
rotation). Scan images are stored at about 2000 pixels per well diameter (camera
pixels are averaged in blocks), so 4x and 10x scans of large wells stay
small. The status line shows the tile count, field of view, and memory for
the current objective, ROI, and binning. Scans are bounded by the **Memory
limit** (same default as the Stage Explorer) and by 2500 tiles. No automatic XY return is
performed. Survey images/settings are not stored in plan JSON.

This feature has synthetic software tests and still needs representative
microscope-image validation. The position tolerance checks measured calibration
wells; it does not independently establish accuracy at distant wells or after
an objective change. See the
[research and implementation plan](plans/hcs-automated-calibration.md).
