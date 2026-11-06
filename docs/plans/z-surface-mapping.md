# Z surface mapping: research and implementation plan

Status: first implementation in progress (2026-11-06): surface model, dialog,
position-table controls, autofocus/Z-plan coordination, per-event Z for positions,
grids and plates, and settings round-trip. See the checklist (§8) and the decision log.

This is an editable working plan. Check off implementation tasks and record changes
in the decision log below. Recommendations are engineering judgments based on the
linked sources and this checkout, not the results of a microscopy benchmark.

## 1. Proposed behavior

Store manually focused XYZ reference points, estimate a surface `z = f(x, y)`, and
add a **Surface Focus** checkbox with an adjacent gear button to the bottom row of
the MDA position table, next to "Include Z" and "HAF Offset". The
gear opens a dialog holding all the surface settings. When the checkbox is
checked, every acquisition event gets `z = f(event.x, event.y)` plus its relative
offsets (Z stack, channel), computed as the events are iterated (§6). The
editable table and the existing acquisition engine are unchanged.

Surface Focus can be combined with **software** autofocus (the surface gives each
position's starting Z, the image-based routine refines it), but not with
**hardware** autofocus (which locks to its own reference whatever the starting Z,
so the surface would be discarded and the per-position AF offsets would be a
second, conflicting definition of the focus).

**Recommended first release:** triangulated linear interpolation by default, an
explicit fitted-plane alternative, at least three non-collinear reference points,
and extrapolation outside the sampled region that follows the fitted plane's
slope (§5, item 6). Do not automatically switch
methods when a fit fails. Nearest neighbour is not the recommended default.

Every grid tile and every HCS plate image position gets the surface Z at its own
XY, in any axis order. `value()` keeps the sequence as edited (positions without
Z, grids, plate plans) and stores the surface in its metadata (§6, §7). Plates are
a primary use case: plate bow and tilt are what a surface map corrects.

## 2. What the current code already does

Paths below are relative to the repository root. Dependency details were checked
against the installed environment as well as adjacent source checkouts; verify
again when implementing because `pyproject.toml` pins custom `cite` branches.

| Location | Relevant behavior / integration consequence |
| --- | --- |
| `src/pymmcore_widgets/useq_widgets/_positions.py` | `PositionTable` owns Include Z, per-position hardware AF offsets, the bottom control row (`_btn_row`: `include_z`, `af_per_position`, stretch, then `_position_actions` with Save/Load), and position-only JSON save/load. Its normal `value()` excludes hidden Z values. The Surface Focus checkbox + gear go in `_btn_row` right after `af_per_position`. |
| `src/pymmcore_widgets/mda/_core_positions.py` | Adds capture-from-core buttons, device enablement, stage navigation, and HCS-generated positions. `value()` can return a `WellPlatePlan` rather than a tuple. In HCS mode it inserts `_hcs_z_info` at the start of `_btn_row` and adds HCS buttons to `_position_actions`. Uses an event filter to track `af_per_position`'s own (ancestor-independent) disabled state; Surface Focus needs the same. |
| `src/pymmcore_widgets/useq_widgets/_mda_sequence.py` | Builds the sequence, manages autofocus and axes, saves JSON/YAML, and constructs fresh `pymmcore_widgets` metadata. Global absolute grids clear position XY. Owns the **Autofocus** card (`AutofocusAxis`, `af_axis`) and its Hardware/Software mode radios. |
| `src/pymmcore_widgets/mda/_core_mda.py` | `MDAWidget.value()` currently fills every position Z from the current focus stage for relative stacks when Include Z is unchecked. The surface branch must take precedence over this fallback, and over `_anchor_plate_z_plan` (which re-centers a relative stack on the current focus for plate plans, whose positions have no Z). Also owns device/AF coordination and run preparation: `_update_autofocus_enablement` already enables `af_per_position` only for hardware mode, and is where Surface Focus enablement should be computed too. |
| `src/pymmcore_widgets/mda/_core_tabs.py` | Coordinates the core-connected axis editors. Its position-table subclass disconnects standalone device handlers because `MDAWidget` owns coordination. |
| `src/pymmcore_widgets/mda/_collapsible_mda.py` | Alternate presentation shares `MDAWidget` behavior; it must inherit the feature without another implementation. |
| `src/pymmcore_widgets/mda/_channel_properties.py` | Existing sequence subclass applies channel device properties. Preserve its behavior; surface-per-position needs no new event wrapper. |
| Installed `useq/_iter_sequence.py::_xyzpos` | Adds relative stack offsets and channel Z offsets to a position's Z, and resolves relative-grid XY separately. This is why replacing `Position.z` is sufficient for the agreed scope. |
| Installed `pymmcore_plus/mda/_engine.py` | Already moves to event Z and applies autofocus corrections. No direct stage movement should be added to `value()`. |

SciPy is neither a declared dependency nor installed in the inspected environment.
NumPy is used already, including least-squares calibration code in
`src/pymmcore_widgets/hcs/_util.py`, but is not a direct project dependency.

## 3. Research: choosing the interpolation method

There is no universally most accurate surface without assumptions about sample
shape, measurement noise, and sampling density. A tilted coverslip and a curved
tissue section call for different models. More mathematical smoothness does not
establish better focus accuracy.

### Method comparison

| Method | Strengths | Limitations for this feature | Recommendation |
| --- | --- | --- | --- |
| Nearest neighbour in XY | Simple; returns a measured height; can operate with one point. | Produces constant-height regions with jumps at their boundaries. Does not reproduce a tilted plane. A distant point can still return a height without meaningful support. | Benchmark baseline; defer as an explicit advanced mode. Never silently use it outside the default surface's coverage. |
| Least-squares plane, `z = ax + by + c` | Reproduces a tilted plane; averages measurement noise with more than three points; easy residual diagnostics. | Cannot follow local curvature; a bad reference can affect the whole fit. Evaluating the equation outside the samples is extrapolation. | Include as **Fitted plane**, for slide tilt and approximately planar samples. |
| Delaunay triangulation + linear interpolation | Accepts irregular XY points; locally uses three reference heights; continuous height across triangle edges; reproduces a plane. | Slope changes across edges; follows measurement noise; large triangles can bridge poorly sampled areas. | **Default**, called **Local linear surface**. |
| Inverse-distance weighting (IDW) | Simple distance-weighted average; bounded by the included sample heights with positive weights. | Requires power/neighbourhood choices; can flatten near measurements; ordinary IDW does not generally reproduce a tilted plane exactly. | Benchmark if needed; no initial UI option. |
| Clough–Tocher cubic | Smooth first derivatives and curved interpolation over scattered 2D points. | Smoothness is not a focus guarantee; no general bound to local sample Z, so assess overshoot. | Future comparison for curved specimens. |
| Thin-plate spline / RBF with smoothing | Smooth flexible surface; regularization can handle noisy measurements. | Smoothing needs selection; can overshoot; global influence and extrapolation require controls. | Future advanced mode, after held-out measurements show a benefit. |
| Global quadratic / higher polynomial | Compact model for broad curvature. | Rank and conditioning matter; increasing degree can create oscillations and large edge errors. | Consider quadratic only after evidence; avoid high-degree default fits. |
| Gaussian-process regression / kriging | Can model spatial correlation and provide model-based predictive uncertainty. | Adds kernel/noise choices and dependencies; uncertainty is conditional on the model. | Research direction for adaptive sampling, beyond this first release. |

Nearest-neighbour behavior is documented by [SciPy](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.NearestNDInterpolator.html)
and the [GDAL interpolation tutorial](https://gdal.org/en/stable/tutorials/gdal_grid_tut.html),
which also specifies IDW. The plane uses [NumPy least squares](https://numpy.org/doc/stable/reference/generated/numpy.linalg.lstsq.html).
The limits and recommendations in the table are our assessment of those methods.

For example, consider a tilted surface `z = 0.01*x` with references at
`(0, 0, 0)`, `(1000, 0, 10)`, and `(0, 1000, 0)`, all in micrometres. At
`(490, 100)`, the true plane height is `4.9 µm`, whereas the closest reference
returns `0 µm`. A fitted plane or linear triangular interpolation reproduces
`4.9 µm` up to floating-point error. This is an illustrative calculation, not
evidence that a particular real specimen is planar.

### Why local linear is the proposed default

[SciPy's LinearNDInterpolator](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.LinearNDInterpolator.html)
uses a triangulation and barycentric interpolation. Within a triangle,
`z = w1*z1 + w2*z2 + w3*z3`, with nonnegative weights summing to one. Thus the
height stays within that triangle's measured height range, apart from numerical
tolerance. It supplies local shape adaptation without cubic overshoot. Its
continuity concerns estimated height; this does not implement continuous stage
motion between acquisition locations.

There is microscopy precedent: the [Micro-Magellan paper](https://www.nature.com/articles/nmeth.3991)
describes surfaces derived from marked points using Delaunay-based interpolation.
Its [official guide](https://micro-manager.org/MicroMagellan) demonstrates creating
a surface from manually focused points. This supports the workflow and family of
methods; it does not establish that our proposed linear interpolator is identical
to Micro-Magellan's full algorithm.

For smooth alternatives, [Clough–Tocher](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.CloughTocher2DInterpolator.html)
provides a continuously differentiable interpolant. [SciPy's RBFInterpolator](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.RBFInterpolator.html)
supports smoothing and a thin-plate kernel with a polynomial term; duplicate and
collinear inputs require care. Its dense formulation also becomes costly as point
counts grow. These are reasons to validate advanced methods rather than choosing
one merely because it looks smooth.

[Zaber's focus-map example](https://software.zaber.com/examples/articles/microscope_focus_map/readme/)
demonstrates polynomial focus fitting with increasing point requirements.
[Gaussian-process documentation](https://scikit-learn.org/stable/modules/gaussian_process.html)
explains probabilistic predictions and kernel fitting. Neither establishes a
universal best method for this microscope or specimen.

### Accuracy and sampling evaluation

- Collect references around the perimeter of the intended XY region, plus points
  inside it where curvature changes. Three points define only a planar patch;
  they cannot demonstrate absence of curvature.
- For meaningful comparison, reserve additional manually focused locations as an
  independent validation set. Measure repeatability at some repeated locations
  to distinguish interpolation error from focusing/stage noise.
- Compare nearest neighbour, plane, local linear, and later smooth candidates on
  tilted-plane, curved, noisy, clustered, and sparse sampling cases. Include real
  validation points; synthetic surfaces alone cannot choose the production model.
- Report absolute Z error: MAE, RMSE, maximum, and a percentile when there are
  enough observations. Also report uncovered locations, sample spacing, build
  time, and evaluation time. Do not drop uncovered points from reporting.
- Set the acceptable Z error from the user's optical/acquisition requirements;
  there is no universal micrometre tolerance. Show plane fit residuals separately
  from independent prediction error. Zero training residual in an interpolator
  says little about unsampled positions.
- Optional later cross-validation must report coverage after withholding points:
  leaving out a hull vertex can put it outside the remaining triangulation.
  Use spatially separated holdouts to avoid an unrealistically easy comparison.
- Do not automatically change the active method as references are added. Persist
  the chosen method and settings so `value()` remains predictable.

## 4. Surface Focus checkbox, gear, and setup dialog

Put the controls together in the position table's existing bottom row, right after
"HAF Offset" (`PositionTable._btn_row`):

```text
[ ] Include Z   [ ] HAF Offset   [ ] Surface Focus [⚙]      Save  Load
```

The checkbox is the single on/off switch for the mode (not a checkbox in every
row). The gear opens the setup dialog; it is always available (when the table is),
even while the checkbox cannot be checked, so the map can be inspected, edited, or
imported. Reference samples are a separate table in that dialog, not acquisition
rows. Nothing is added to the Autofocus card or the settings column.

The checkbox and gear belong to `PositionTable` (core-independent: dialog, stored
map, checked state); capture-from-core and device checks belong to the core
subclass, and the cross-widget enablement (Autofocus, Z plan) to `MDAWidget`. The
row is already crowded, so check it at typical widths in both MDA layouts.

In HCS mode, `_set_position_table_editable(False)` hides Include Z and "HAF
Offset" and shows the `_hcs_z_info` label instead. Surface Focus **stays
visible** there:

```text
Z: focus position at run time   [ ] Surface Focus [⚙]      Editable  HCS  Save  Load
```

While Surface Focus is checked, the label and its tooltip change to say that each
plate position gets its Z from the surface (e.g. "Z: from Surface Focus"), and
that a relative Z stack is centered on it.

### Combination with Autofocus

Surface Focus combines with **software** autofocus and excludes **hardware**
autofocus:

- **Software autofocus + Surface Focus (allowed):** each position starts at its
  surface Z and the routine refines it from there (see §6). A good starting point
  allows a much smaller search range (fewer images, less bleaching, faster) and
  makes locking onto a wrong plane (debris, plate bottom) less likely. The dialog
  should suggest a small search range in this mode.
- **Hardware autofocus + Surface Focus (not allowed):** hardware autofocus locks to
  its reference (e.g. the coverslip reflection plus its offset) whatever the
  starting Z, so the surface Z would only decide where the lock attempt begins, and
  the per-position AF offsets would be a second, conflicting definition of the
  focus. The engine already steps through a search range when `fullFocus` fails;
  an explicit "pre-focus hardware autofocus with the surface" option can be added
  later if capture range proves a problem on real (e.g. bowed) plates.

UI rules (the Autofocus card has Hardware/Software mode radio buttons inside it,
which are disabled while the card is unchecked; the "HAF Offset" checkbox (per-
position hardware autofocus offsets) is enabled whenever Hardware mode is
selected, even with the card unchecked):

- While the **Autofocus** card is checked in **Hardware** mode, the Surface Focus
  checkbox is disabled with the tooltip **"Cannot use while hardware 'Autofocus'
  is checked."** In Software mode (or unchecked), Surface Focus is available.
- While **"HAF Offset"** is checked (and enabled), the Surface Focus checkbox is
  disabled with the tooltip **"Cannot use while 'HAF Offset' is checked."**:
  per-position offsets request hardware autofocus at each position.
- While **Surface Focus** is checked:
  - the Autofocus card's **Hardware** option is disabled with the tooltip
    **"Hardware autofocus cannot be used while 'Surface Focus' is checked."**;
  - checking the Autofocus card selects **Software** mode if Hardware was the
    selected (inactive) mode. This is visible in the card, and needed: otherwise
    the unchecked card could neither be checked nor switched to Software;
  - "HAF Offset" is disabled, with the same tooltip.
- Unchecking Surface Focus re-enables the Hardware option and "HAF Offset"
  (restoring their normal tooltips) but does not switch the mode back.
  Disabling a control never changes another control's checked state.
- Device-based enablement still applies on top (e.g. a missing autofocus device,
  which only matters for hardware mode). Keep one function in `MDAWidget` (next to
  `_update_autofocus_enablement`) that computes the enabled state and tooltip of
  each control from (checked states, autofocus mode, devices), rather than
  separate slots that can overwrite each other.
- Disabled-by-the-other must be distinguished from a disabled ancestor while an MDA
  runs (see the existing `af_per_position` event filter regression), so that all
  controls come back in the right state after a run.
- `setValue()`/settings loads that contain both a **hardware** autofocus plan (or
  per-position AF offsets) and enabled surface metadata are invalid: keep the
  autofocus plan, load the surface with its checkbox unchecked, and report why. A
  software autofocus plan with an enabled surface is valid. `value()`/run
  preparation also validate this, since programmatic settings can bypass the UI.

### Other enablement and precedence

| Condition | Proposed result |
| --- | --- |
| Include Z checked | Surface Focus cannot be checked (tooltip says why). Keep the stored map. Symmetrically, checking Surface Focus unchecks and disables Include Z ("Cannot use while 'Surface Focus' is checked.") until Surface Focus is unchecked; positions loaded meanwhile keep their Z, unused. |
| Autofocus card checked in Hardware mode | Surface Focus disabled: "Cannot use while hardware 'Autofocus' is checked." |
| "HAF Offset" checked | Surface Focus disabled: "Cannot use while 'HAF Offset' is checked." |
| Autofocus card checked in Software mode | Surface Focus available: surface Z is the starting point, software autofocus refines it. |
| Surface Focus checked | Autofocus Hardware option and "HAF Offset" disabled: "Hardware autofocus cannot be used while 'Surface Focus' is checked." Software autofocus stays available. |
| Autofocus device loaded but Autofocus unchecked | Surface Focus available. Existing run preparation handles a physically engaged continuous-focus device before acquisition. |
| No XY or focus device | Disable activation and capture, retaining references. The gear still opens the dialog for inspecting/editing/importing the map. |
| Invalid or incomplete surface | The checkbox stays enabled but cannot be checked: clicking it opens the setup dialog, and it is checked once the surface is valid. The tooltip explains the problem. |
| Position axis unchecked or no selected acquisition positions | `MDAWidget` uses the current stage position (a relative grid is centered on it); an absolute grid gives its tiles' XY. The base widget, without a stage, needs positions or an absolute grid. |
| Absolute Z plan selected | Disable surface mode: an absolute stack ignores the surface baseline. Relative stacks remain supported. |
| Global absolute grid or selected row with an absolute sub-grid | Allow: each tile's event has its XY; never evaluate disabled display-only row XY. |
| Relative grid | Allow by adding each tile's XY offset to its parent/origin XY and evaluating surface Z there. |
| HCS `WellPlatePlan` | Allow: each image position's events get the surface Z at its XY; the plate plan is kept (§6). The checkbox stays visible in HCS mode; `_hcs_z_info` explains where Z comes from. |
| Acquisition running | Disable edits and capture using the existing run lifecycle. Restore correct enablement afterwards. |

If a conflicting option (Include Z, an absolute Z plan) becomes active while
Surface Focus is checked, uncheck Surface Focus, retain its map, and update its
tooltip. Do not silently re-check it when the conflict disappears. Centralize this
coordination to avoid signal loops between the position, autofocus, surface, and
Z-plan widgets.

### Setup dialog (gear)

- Numbered reference table on the left: X [µm], Y [µm], Z [µm] (no names). Store
  finite float values at full precision regardless of display rounding.
- As in the position table, with a stage the **+** button adds a row at the
  current XYZ, and an arrow column sets a row to it, after a manual focus
  adjustment. Remove, clear, and manual entry support refinement. Reading/capturing does not move
  stages; do not add implicit movement when a sample row is selected.
- Import/export a versioned surface JSON file, independent of position-list JSON.
- Read-only summary: valid reference count, XY coverage, Z range, fit validity,
  and plane residuals where applicable, plus why the checkbox cannot be checked,
  if it cannot, with a status icon (green check if valid, red cross if not).
- On the right, the method selector above a color-coded 2D map of the surface Z
  (numbered points, colorbar, hover tooltip with X/Y/Z), drawn with QPainter; it
  is more useful than a 3D view and needs no OpenGL. Still open: overlay the
  acquisition locations.
- The map outlines the measured area (the points' hull) and dims the extrapolated
  area around it. Still open: show the acquisition positions/tiles on it, flagging
  extrapolated ones, from the events' XY.
- OK commits valid edits atomically; Cancel discards changes. Dialog edits cannot
  change an in-progress acquisition snapshot. Opening or accepting the dialog does
  not automatically check Surface Focus.
- Give the checkbox a short status tooltip (e.g. "Linear surface, 6 points"), so
  the current map is visible without opening the dialog.

Use `qtpy` imports, existing icon/button conventions (`QIconifyIcon`, as the
software-autofocus settings gear), standard layouts, accessible labels/tooltips,
and no new `setStyleSheet` calls. Account for the extra controls in both tabbed
and collapsible layouts (`MDAWidget` and `MDAWidgetCollapsible`, and
pymmcore-gui's subclasses).

## 5. Surface model and coverage rules

Create a Qt-independent module, tentatively `src/pymmcore_widgets/_z_surface.py`,
with a validated serializable definition and a separate compiled interpolator.

```text
SurfacePoint: x_um, y_um, z_um
ZSurfaceDefinition:
    schema_version = 1
    points: list[SurfacePoint]
    method: "linear" | "plane"
    context: XY device, focus device, optional configuration/objective identity
CompiledZSurface:
    evaluate(xy_array) -> z_array
    coverage(xy_array) -> validity/diagnostics
```

Cache compilation by an immutable definition/revision, not mutable table identity.
Keep arrays, triangulation objects, and fitted coefficients out of saved settings;
rebuild them from reference points and method settings.

Validation and numerical policy:

1. Require at least three distinct, non-collinear XY points for either shipped
   method. Reject non-finite coordinates and heights. One/two-point maps can be
   saved as drafts but cannot be activated.
2. Detect duplicate and near-duplicate XY with a documented tolerance informed by
   stage resolution. Request replacement of an existing sample or explicit user
   editing; never silently average contradictory heights. A multi-valued surface
   at the same XY cannot be represented by `z = f(x, y)`.
3. Center coordinates and use one common XY scale for numerical stability while
   preserving physical Euclidean geometry. Independently normalizing X and Y can
   change the triangulation; do not enable it automatically.
4. Fit the plane with a stable least-squares solver; inspect rank/conditioning
   and compute residuals explicitly, including the exactly-three-point case.
   Reject nearly collinear layouts with actionable diagnostics.
5. Catch triangulation failures and explain them. [SciPy's Delaunay documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.Delaunay.html)
   describes degeneracy and finite-precision caveats. Test hull edges and sample
   vertices explicitly; do not hide invalid geometry with random jitter.
6. Inside the reference points' convex hull (with a documented numerical
   tolerance), the methods interpolate. Outside it, Z is extrapolated: the plane
   is evaluated as is, and the linear surface continues from the nearest point of
   the hull with the fitted plane's slope, so it has no step at the hull and
   follows the sample tilt (3 points give exactly the plane). The dialog marks the
   extrapolated area.
7. An interior location can still be poorly supported. Show large triangle spans
   and nearest-reference distance; consider a user-set maximum support span after
   initial measurement trials. Do not invent a universal distance cutoff.
8. A hull can bridge gaps, holes, and distinct wells. A single surface is suitable
   only for one physically coherent region. A plate bottom is one: references
   focused in several wells (e.g. the corners and the centre of the used area)
   map its tilt and bow across the plate. It is not one when the samples
   themselves sit at different heights per well (e.g. gels or tissue of varying
   thickness); native per-well surfaces/masks for that are future work, and
   nearest XY alone does not identify the correct sample region.
9. Device/configuration changes mark the map as requiring review before reuse.
   Stage re-zeroing, sample replacement, or changed optics can invalidate it even
   if device labels match. Provide a clear way to reset/revalidate its context.

Dependency choice (revised 2026-11-06): the surface lives in useq-schema, which
depends only on NumPy, so its triangulation is NumPy-only (`useq/_focus_surface.py`):
a sweep over the points sorted by (x, y), then Lawson edge flips. In general
position the Delaunay triangulation is unique, and the interpolation matches
SciPy's `LinearNDInterpolator` to ~1e-13 (300 random sets checked). Cocircular
points (rectangle corners, regular grids) have several Delaunay triangulations:
Qhull's choice is an implementation detail, ours keeps the sweep's split, so it
does not depend on the order of the points. No SciPy dependency is needed.

## 6. Sequence construction: `MDASequence.focus_surface` (useq-schema)

The surface is part of the schema: `useq.FocusSurface(points=[(x, y, z), ...],
method="linear" | "plane")` in `MDASequence.focus_surface`. useq validates it
(at least 3 points, no duplicate XY, not on a line) and rejects it with an
absolute Z plan. While iterating (`useq/_iter_sequence.py::_xyzpos`), an event at
a position without its own `z` gets the surface Z at the event's XY as its base
Z, then the relative Z plan and channel offsets are added as usual. A position's
own `z` wins. Surface Z is computed once per distinct XY. So any runner
(pymmcore-plus or other) follows the surface from a plain saved sequence, and:

- **Grids:** every tile gets the surface Z at its own XY, in **any** axis order
  (e.g. `t,p,c,g,z`), with the `g` axis and grid indices intact.
- **Autofocus:** any software autofocus schedule (`p`, `g`, intervals) works
  unchanged: the event Z is where the routine starts, and the pymmcore-plus engine
  keeps its result as a per-position correction. Hardware autofocus is not
  combined with Surface Focus (§4); that is the widget's rule, not useq's.
- **Plate plans:** each image position gets the surface Z at its XY, and the
  `WellPlatePlan` is kept, so writers get the full plate layout and FOV indices.

In pymmcore-widgets, `MDASequenceWidget.value()` sets `focus_surface` while
Surface Focus is checked and keeps positions without Z. `MDAWidget` gives the
current stage position no Z when it adds it (no positions), does not fill the
positions' Z from the current focus, and does not anchor a plate's relative stack
to the current Z. `_z_surface.ZSurface` is the editor's state (possibly incomplete,
e.g. 2 points); `ZSurface.focus_surface()` builds the useq model and `problem()`
returns useq's validation message.

Plate coverage: image positions are spread over each well, so references at well
centres leave the outer FOVs of edge wells outside the hull: their Z is
extrapolated with the plate tilt (§5). The user guide should still suggest placing
references near the outer edge of the outermost selected wells. The surface uses
the calibrated stage XY, so recalibrating the plate moves the image positions and
their Z follows; re-seating the plate invalidates the references themselves (§5,
item 9).

### Validation timing and responsiveness

The application calls `value()` for more than Run. Cache fitting, vectorize
evaluation, and keep routine enablement checks independent of full sequence
construction. Validation does not iterate the events, and the surface Z is
computed once per distinct XY while iterating. Avoid recursive
`valueChanged -> value() -> valueChanged` paths.

An active map that becomes invalid must not silently revert to
current stage Z or output partially resolved positions. Return a clear validation
error; make preview/save callers display it without crashing. In run preparation,
validate before creating output destinations or moving hardware. Check the final
surface + stack + channel Z range against reliable device/user limits when
available; never silently clamp Z values.

Commit editor text before resolving positions, as the existing Run/Save paths do.
Resolve a single immutable sequence snapshot for a run so preparation, output
metadata, and execution agree. Verify whether passing that prepared snapshot
through `execute_mda` requires a small internal refactor while retaining its
public subclass contract. Do not change a map in response to stage movement
during acquisition. Recompute from current committed map/XY on each later call
to `value()`; no drift tracking is implied.

## 7. Persistence and compatibility

An active surface is the sequence's `focus_surface` field (§6), so the saved
sequence is usable by any useq consumer. A surface that is set up but not used
(Surface Focus unchecked) is kept in
`MDASequence.metadata["pymmcore_widgets"]["z_surface"]` (`{"method", "points"}`,
the same JSON as a `FocusSurface`) so turning the feature off does not discard it.
The JSON example below is the original proposal, kept for reference.

Illustrative metadata shape (three points shown only as a format example):

```json
{
  "schema_version": 1,
  "enabled": true,
  "method": "linear",
  "units": "um",
  "points": [
    {"x_um": 0.0, "y_um": 0.0, "z_um": 10.0},
    {"x_um": 1000.0, "y_um": 0.0, "z_um": 20.0},
    {"x_um": 0.0, "y_um": 1000.0, "z_um": 10.0}
  ],
  "context": {"xy_device": "XY", "focus_device": "Z"}
}
```

- Write this metadata after the base widget builds its fresh metadata. Preserve
  saving settings and per-channel device properties. Serialize the map even when
  disabled so turning the feature off does not discard setup work.
- The sequence fields are the editor's (positions without Z, grids, plate plan),
  so loading restores the editor directly; no separate source plan is needed. A
  saved plate plan reopens in HCS mode, not as editable rows. Restore
  rows and AF/Z plans, then restore the map and mode atomically.
  `PositionTable.setValue()` infers Include Z from non-null Z; surface-generated Z
  would otherwise incorrectly reactivate Include Z. When valid enabled surface
  metadata is present, explicitly restore Include Z as unchecked and reapply the
  surface compatibility checks before emitting a final change notification.
- Absent surface metadata means the legacy behavior. Loading legacy settings into
  a widget that previously had an active map must clear/deactivate that old map.
  Unknown versions, invalid definitions, and incompatible settings must produce
  an actionable message and leave surface activation off, not reinterpret data.
- In memory, turning the checkbox off preserves existing manual Z cells. For the
  initial saved-settings format, the main sequence contains resolved Z values;
  do not promise recovery of pre-surface hidden manual Z after reloading. If that
  recovery is desired, add an explicitly versioned editor-state field and tests.
- Keep position-only JSON backward compatible. Export positions from the parent
  MDA's resolved snapshot when a surface is active, or clearly label the existing
  table export as XY-only and direct users to full MDA settings for resolved Z.
  **Recommended:** route the active-surface export through the resolved snapshot.
  Loading a bare position list restores fixed coordinates, not an editable map;
  the dedicated surface file carries reference points and method settings.
- Imported maps never activate themselves. Validate their context and coverage
  against the current setup before selection.

## 8. Implementation checklist

### Phase 1: numerical model and evidence

- [x] Add the serializable definition, compiled model, validation, coverage, and
  plane/linear evaluators with no Qt dependency.
- [x] Declare numerical dependencies and regenerate the lockfile with `uv`.
- [x] Add meaningful tests for planar reproduction, reference vertices, hull
  boundaries, duplicate/non-finite/collinear inputs, and out-of-hull extrapolation.
- [ ] Record a reproducible method comparison using held-out points. Keep the
  distinction between synthetic checks and measured microscope accuracy.

### Phase 2: setup and position-table controls

- [x] Add the Surface Focus checkbox + gear to the position table's bottom row
  (after "HAF Offset"), the setup dialog, capture/edit actions,
  and surface JSON.
- [x] Allow Surface Focus with software autofocus; make it exclusive with hardware
  autofocus (disabled Hardware option / disabled Surface Focus checkbox, mutual
  exclusion with "HAF Offset", Software selected when checking
  Autofocus), with the tooltips from §4, combined with device-based enablement,
  and restored correctly after a run.
- [x] Separate core-independent table/dialog state from core capture/device checks;
  keep MDA-level AF/Z-plan compatibility in the MDA owner.
- [x] Implement atomic dialog Apply/Cancel behavior and cached compilation.
- [x] Add centralized mode coordination (Autofocus, Include Z, Z plan), descriptive
  disabled tooltips, the HCS-mode label/visibility, and unsupported-order
  restrictions. Preserve references across mode changes.

### Phase 3: `value()`, saving, and execution

- [x] Insert the surface branch ahead of the current-relative-Z fallback and
  preserve existing sequence fields, channel properties, and manual table cells.
- [x] Add `useq.FocusSurface` and `MDASequence.focus_surface` to useq-schema
  (NumPy-only), giving each event the surface Z at its XY plus its offsets, for
  positions, grids in any axis order and plate plans; use it from the widgets.
- [x] Support no stack, relative stacks, and channel offsets on every event;
  retain implicit current-position behavior when no spatial plan is used.
- [ ] Add metadata round-trip tests for legacy reset behavior, and decide whether
  to export resolved positions (each with its surface Z).
- [ ] Add preflight error presentation, final target-range checks where supported,
  and consistent prepared/run snapshots without breaking subclass APIs.

### Phase 4: integration validation and documentation

- [ ] Test both MDA presentations and standalone/core-connected table behavior,
  including the bottom row's layout at typical widths and in HCS mode.
- [ ] Test device reload, focus/XY removal, mode conflicts, run disable/re-enable,
  and repeated settings loads. Verify hardware autofocus and Surface Focus can
  never be active together (UI, `setValue`, and `value()`), with the expected
  tooltips, and that software autofocus + Surface Focus is allowed.
- [ ] With the demo core and a software autofocus routine, verify each position
  starts at its surface Z, the routine's correction is kept per position, and
  later events/stack planes use surface Z plus it.
- [ ] With the demo core, verify emitted event Z for multiple positions, relative
  stacks, channel offsets, non-stacking channels, and per-tile grid following.
- [ ] Verify the written output (grids, plate layout) with save writers and
  AF/shutter schedules; repeated load/save must restore the editor unchanged.
- [ ] With the demo core and an HCS plate plan (single point, grid, and Cover Area
  per well): verify per-FOV surface Z, unchanged event order and AF
  schedule, relative stacks centered on surface Z, extrapolated edge-well FOVs, `_hcs_z_info` text, and the plate layout in the written output.
- [ ] Verify channel property events still contain properties.
- [ ] Verify `value()` leaves widget/manual Z state unchanged, is deterministic
  for fixed inputs, and raises for unsupported sequences before acquisition
  starts. Test bounded handling of very large grids.
- [ ] Test JSON/YAML round-trip, map-disabled persistence, conflicting imported
  settings, unknown schema versions, and legacy files without map metadata.
- [ ] Add a minimal example and user guide explaining reference placement,
  coverage, extrapolation, and when refocusing/re-mapping is needed.
- [ ] Run `uv run prek -a && uv run pytest` and require CI to pass before merge.

Existing tests to extend: `tests/test_useq_core_widgets.py` (including
`test_relative_z_with_no_include_z`, HCS, AF, and channel property tests),
`tests/useq_widgets/test_useq_widgets.py`, `tests/test_mda_widget_tabs.py`, and
`tests/test_collapsible_mda.py`. Add focused numerical and dialog test modules.

Suggested new files: `_z_surface.py`,
`useq_widgets/_z_surface_dialog.py`, `tests/test_z_surface.py`, and
`tests/useq_widgets/test_z_surface_dialog.py`. Final public names can be chosen
when implementing; reuse existing table infrastructure where practical.

## 9. Later extensions and decisions to revisit

- **Z in the sequence itself:** if useq gains per-position/per-tile Z callbacks
  or fields, the surface could live in the plain sequence instead of a subclass,
  so a saved sequence would follow it without pymmcore-widgets.
- **Multiple regions/surfaces:** assign by region/well rather than allowing a
  triangulation to bridge unrelated samples.
- **Smooth fitting:** introduce a regularized thin-plate or cubic option only
  after independent focus validation, with explicit smoothing and overshoot policy.
- **Coverage extensions:** a maximum extrapolation distance, nearest fallback,
  maximum support span, or constant/line maps must be named user choices.
- **Adaptive sampling or drift:** separate static map estimation from automatic
  autofocus sampling and time-dependent correction.
- **Editor fidelity on settings reload:** decide whether hidden manual Z values
  need independent persistence as well as the resolved acquisition coordinates.

### Decision log

| Date | Decision | Reason / follow-up |
| --- | --- | --- |
| 2026-11-04 | Resolve one Z per position in `MDAWidget.value()`. | User clarified the intended implementation; ordinary useq Z handling already composes stacks/offsets. |
| 2026-11-04 | Recommend linear triangulation + optional plane. | Predictable local interpolation and an explicit model for slide tilt; validate accuracy on real held-out points. |
| 2026-11-04 | Expand grids into individual positions before resolving Z. | User proposed flattening in `value()`; this enables per-tile following with ordinary useq sequences. Supersedes the earlier shared-grid-baseline proposal. Preserve source grids in editor metadata and handle axis/scheduling semantics explicitly. |
| 2026-11-04 | Default to hull-limited coverage and explicit failure. | Avoid silently generating Z from unsupported XY or switching methods. |
| 2026-11-06 | Put the surface controls in a checkable **Surface Focus** card under the Autofocus card (not in the position table). | One place for the focus strategy, next to Autofocus. Supersedes the table checkbox + gear. **Superseded below (same day).** |
| 2026-11-06 | Allow Surface Focus with **software** autofocus; exclude **hardware** autofocus. | Software AF starts from the event Z, so the surface makes its search smaller, faster and more robust (engine keeps per-position corrections). Hardware AF locks to its own reference whatever the start Z (the surface would be discarded, and per-position AF offsets would conflict with it). Tooltips: "Cannot use while hardware 'Autofocus' is checked." / "Hardware autofocus cannot be used while 'Surface Focus' is checked." Revisit "surface pre-focus for hardware AF" if capture range is a problem on real plates. |
| 2026-11-06 | Go back to a **Surface Focus** checkbox + gear button in the position table's bottom row (next to Include Z and "HAF Offset"); all settings in the gear's dialog. No card under Autofocus. | User decision: the surface is a property of the positions, so it belongs with the position table's Z options. Keeps the software/hardware autofocus rules above; "HAF Offset" and Surface Focus are mutually exclusive. |
| 2026-11-06 | Support HCS `WellPlatePlan`s instead of deferring them. | User decision: plates are a main use case (plate tilt/bow). A plate plan is already one `p` axis, so `value()` expands it to its image positions with surface Z and no axis/AF remapping. The checkbox stays visible in HCS mode. Open: how to keep the full plate layout and FOV grid indices for writers (see §6, "Plate plans"). |
| 2026-11-06 | SciPy is an optional extra, `surface` (also kept in `hcs-auto`); NumPy is a direct dependency. | User decision, following `hcs-auto`. SciPy is imported only when a surface is compiled; without it the checkbox is disabled and its tooltip says how to install it. |
| 2026-11-06 | Core-independent rules and `value()` resolution live in `MDASequenceWidget`, not only `MDAWidget`. | The standalone widget has the same checkbox, so it must behave the same. `MDAWidget` adds device checks, capture, and the current XY as the origin of a relative grid without positions. `AutofocusAxis.setHardwareBlocked` keeps the settings conflict separate from device availability. |
| 2026-11-06 | Flattened tile names are `<parent>_<tile>` (`Pos<p>_<tile>` for unnamed parents); no separate provenance map yet. | `grid_row`/`grid_col` are kept on each tile. Autofocus is only remapped for "every tile" (`g`, intervals of 1); other `p`/`g` schedules are rejected. A plain position in a mixed list also gets autofocus once `g` becomes `p`. |
| 2026-11-06 | `metadata["pymmcore_widgets"]["z_surface"]` stores the map, `enabled`, and `source` (positions, grid, axis order, AF plan before expansion); `setValue` restores the editor from `source`. | Invalid or unknown-version metadata, and an enabled surface with hardware autofocus, warn and load with Surface Focus off. Still open: XY plot and uncovered-position preview in the dialog, device context/review (§5.9), resolved position-list export, writer plate layout (§6). |
| 2026-11-06 | An incomplete surface no longer disables the Surface Focus checkbox: clicking it opens the setup dialog. | User feedback: a disabled checkbox with no points looked broken, and nothing pointed to the gear. Only real conflicts (Include Z, HAF Offset, hardware autofocus, absolute Z, missing devices) disable it. |
| 2026-11-06 | Extrapolate outside the points' hull instead of refusing those positions: the plane as is, the linear surface from the nearest hull point with the fitted plane's slope. | User decision: e.g. 4 points in one well must still give a good Z just beyond them. Nearest neighbour was considered, but it ignores the tilt. Supersedes the hull-limited coverage of 2026-11-04. The map dims the extrapolated area and its tooltip says so. |
| 2026-11-06 | Compute Z per event when iterating (`ZSurfaceSequence`) instead of flattening grids and plates in `value()`. | User decision: flattening could not keep orders with other axes between `p` and `g` (e.g. `p,c,g`) nor per-parent AF schedules, and lost the plate layout and grid indices for writers. `value()` now returns the sequence as edited, without Z; supersedes the 2026-11-04 expansion decision and the tile-naming, `source` metadata and AF-remap rows. A saved sequence needs `ZSurfaceSequence` to follow the surface outside the widget. |
| 2026-11-06 | Move the surface into useq-schema: `useq.FocusSurface` in `MDASequence.focus_surface`, evaluated by useq's event iteration, with a NumPy-only triangulation. | User decision: any runner follows a saved sequence, and the widgets lose `ZSurfaceSequence`, the per-event code, the `enabled` metadata and the SciPy extra. Supersedes the SciPy-extra and `ZSurfaceSequence` rows. Needs the useq-schema `cite` branch pushed and re-locked. |
