# Z surface mapping: research and implementation plan

Status: proposal; no feature code implemented. Updated: 2026-11-06 (Surface Focus
card under Autofocus; combinable with software autofocus, exclusive with hardware
autofocus; see §4 and the decision log).

This is an editable working plan. Check off implementation tasks and record changes
in the decision log below. Recommendations are engineering judgments based on the
linked sources and this checkout, not the results of a microscopy benchmark.

## 1. Proposed behavior

Store manually focused XYZ reference points, estimate a surface `z = f(x, y)`, and
add a checkable **Surface Focus** card to the MDA widget, directly under the
existing **Autofocus** card. All the surface controls live in that card. When it is
checked, `MDAWidget.value()` returns a sequence whose positions have
`z = f(position.x, position.y)`. Update copies of the returned positions, preserving
the editable table and the existing acquisition engine.

Surface Focus can be combined with **software** autofocus (the surface gives each
position's starting Z, the image-based routine refines it), but not with
**hardware** autofocus (which locks to its own reference whatever the starting Z,
so the surface would be discarded and the per-position AF offsets would be a
second, conflicting definition of the focus).

**Recommended first release:** triangulated linear interpolation by default, an
explicit fitted-plane alternative, at least three non-collinear reference points,
and no extrapolation outside the sampled region. Do not automatically switch
methods when a fit fails. Nearest neighbour is not the recommended default.

The user's clarified approach is **one surface-derived Z per final position**,
computed in `value()`. When grids are present, expand their tiles into individual
absolute stage positions first, then compute surface Z at every tile's XY. Remove
the grids from the returned acquisition sequence. Keep the original editable rows
and grid definitions in the widget and saved editor metadata.

## 2. What the current code already does

Paths below are relative to the repository root. Dependency details were checked
against the installed environment as well as adjacent source checkouts; verify
again when implementing because `pyproject.toml` pins custom `cite` branches.

| Location | Relevant behavior / integration consequence |
| --- | --- |
| `src/pymmcore_widgets/useq_widgets/_positions.py` | `PositionTable` owns Include Z, per-position hardware AF offsets, the bottom control row, and position-only JSON save/load. Its normal `value()` excludes hidden Z values. |
| `src/pymmcore_widgets/mda/_core_positions.py` | Adds capture-from-core buttons, device enablement, stage navigation, and HCS-generated positions. `value()` can return a `WellPlatePlan` rather than a tuple. |
| `src/pymmcore_widgets/useq_widgets/_mda_sequence.py` | Builds the sequence, manages autofocus and axes, saves JSON/YAML, and constructs fresh `pymmcore_widgets` metadata. Global absolute grids clear position XY. The **Autofocus** card (`AutofocusAxis`, `af_axis`) is added to the settings column (`_settings_row`); `_enable_af` sets its enablement/tooltip from device availability. The Surface Focus card goes right after it. |
| `src/pymmcore_widgets/useq_widgets/_axis_tabs.py` | `_CheckableCard` (a checkable `QGroupBox`) is the base of the Autofocus card; reuse it for Surface Focus so both cards look and behave alike. |
| `src/pymmcore_widgets/mda/_core_mda.py` | `MDAWidget.value()` currently fills every position Z from the current focus stage for relative stacks when Include Z is unchecked. The surface branch must take precedence over this fallback. Also owns device/AF coordination and run preparation. |
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

## 4. Surface Focus card and setup

Add a checkable **Surface Focus** card (same `_CheckableCard` base as the
Autofocus card) to the MDA widget's settings column, directly **under the
Autofocus card**. All surface controls go in this card; nothing is added to the
position table.

```text
[x] Autofocus        (existing card: axes, hardware/software mode, search range)
[ ] Surface Focus    (new card: method, reference points, status, setup)
```

The card is the single on/off switch for the mode (not a checkbox per row).
Reference samples are a separate list edited from the card, not acquisition rows.

### Combination with Autofocus

Surface Focus combines with **software** autofocus and excludes **hardware**
autofocus:

- **Software autofocus + Surface Focus (allowed):** each position starts at its
  surface Z and the routine refines it from there (see §6). A good starting point
  allows a much smaller search range (fewer images, less bleaching, faster) and
  makes locking onto a wrong plane (debris, plate bottom) less likely. The card
  should suggest a small search range in this mode.
- **Hardware autofocus + Surface Focus (not allowed):** hardware autofocus locks to
  its reference (e.g. the coverslip reflection plus its offset) whatever the
  starting Z, so the surface Z would only decide where the lock attempt begins, and
  the per-position AF offsets would be a second, conflicting definition of the
  focus. The engine already steps through a search range when `fullFocus` fails;
  an explicit "pre-focus hardware autofocus with the surface" option can be added
  later if capture range proves a problem on real (e.g. bowed) plates.

UI rules (the Autofocus card has Hardware/Software mode radio buttons inside it,
which are disabled while the card is unchecked):

- While the **Autofocus** card is checked in **Hardware** mode, the Surface Focus
  card is disabled with the tooltip **"Cannot use while hardware 'Autofocus' is
  checked."** In Software mode (or unchecked), Surface Focus is available.
- While **Surface Focus** is checked:
  - the Autofocus card's **Hardware** option is disabled with the tooltip
    **"Hardware autofocus cannot be used while 'Surface Focus' is checked."**;
  - checking the Autofocus card selects **Software** mode if Hardware was the
    selected (inactive) mode. This is visible in the card, and needed: otherwise
    the unchecked card could neither be checked nor switched to Software;
  - "Set AF Offset per Position" (hardware offsets) is disabled in the position
    table, with the same tooltip.
- Unchecking Surface Focus re-enables the Hardware option and the per-position
  offsets (restoring their normal tooltips) but does not switch the mode back.
  Disabling a control never changes the other card's checked state.
- Device-based enablement still applies on top (e.g. `_enable_af` and a missing
  autofocus device, which only matters for hardware mode). Keep one function that
  computes the enabled state and tooltip of each control from (checked states,
  autofocus mode, devices), rather than separate slots that can overwrite each
  other.
- Disabled-by-the-other must be distinguished from a disabled ancestor while an MDA
  runs (see the existing AF-per-position event filter regression), so that all
  controls come back in the right state after a run.
- `setValue()`/settings loads that contain both a **hardware** autofocus plan and
  enabled surface metadata are invalid: keep the autofocus plan, load the surface
  with its card unchecked, and report why. A software autofocus plan with an
  enabled surface is valid. `value()`/run preparation also validate this, since
  programmatic settings can bypass the UI.

### Other enablement and precedence

| Condition | Proposed result |
| --- | --- |
| Include Z checked | Surface Focus cannot be checked (tooltip says why). Keep the stored map. |
| Autofocus card checked in Hardware mode | Surface Focus disabled: "Cannot use while hardware 'Autofocus' is checked." |
| Autofocus card checked in Software mode | Surface Focus available: surface Z is the starting point, software autofocus refines it. |
| Surface Focus checked | Autofocus Hardware option and "Set AF Offset per Position" disabled: "Hardware autofocus cannot be used while 'Surface Focus' is checked." Software autofocus stays available. |
| Autofocus device loaded but Autofocus unchecked | Surface Focus available. Existing run preparation handles a physically engaged continuous-focus device before acquisition. |
| No XY or focus device | Disable activation and capture, retaining references. The card's setup controls still allow inspecting/editing/importing the map. |
| Invalid or incomplete surface | Disable activation and explain the problem; setup controls stay available to fix it. |
| Position axis unchecked or no selected acquisition positions | A selected grid can still be expanded: absolute grids provide XY directly; relative grids use a snapshot of the current XY as origin. Without a grid or selected positions, retain the existing implicit-current-position fallback without applying the surface. |
| Absolute Z plan selected | Disable surface mode: an absolute stack ignores the surface baseline. Relative stacks remain supported. |
| Global absolute grid or selected row with an absolute sub-grid | Allow by expanding the actual grid coordinates into positions; never evaluate disabled display-only row XY. |
| Relative grid | Allow by adding each tile's XY offset to its parent/origin XY and evaluating surface Z there. |
| Axis order that cannot be preserved by the supported grid-to-position expansion | Explain the restriction and require a supported order. Do not silently reorder acquisition. |
| HCS `WellPlatePlan` | Defer native support. Explain that the user must convert to editable positions first; do not silently materialize or change a plate plan. (Plate positions have no Z; a relative stack is anchored to the current focus at run time.) |
| Acquisition running | Disable edits and capture using the existing run lifecycle. Restore correct enablement afterwards. |

If a conflicting option (Include Z, an absolute Z plan) becomes active while
Surface Focus is checked, uncheck Surface Focus, retain its map, and update its
tooltip/status. Do not silently re-check it when the conflict disappears.
Centralize this coordination to avoid signal loops between the position, autofocus,
surface, and Z-plan widgets.

### Surface Focus card contents

The card holds compact controls; the full reference table opens in a dialog when
it needs more room:

- Method selector: **Local linear surface** (default), **Fitted plane**.
- Status: valid reference count, XY coverage, Z range, fit validity (and plane
  residuals), plus why the card cannot be checked, if it cannot.
- **Add Current XYZ** (captures a reference point from the current stage and focus
  position, without moving anything) and **Edit Points…** (opens the dialog below).

### Reference points dialog

- Editable reference table: optional name, X [µm], Y [µm], Z [µm]. Store finite
  float values at full precision regardless of display rounding.
- **Add current XYZ** captures all coordinates from the same configured devices
  after a manual focus adjustment. **Update selected from current XYZ**, remove,
  clear, and manual entry support refinement. Reading/capturing does not move
  stages; do not add implicit movement when a sample row is selected.
- Import/export a versioned surface JSON file, independent of position-list JSON.
- An optional XY plot shows samples, hull, and expanded acquisition locations; it
  is more useful initially than a 3D view.
- Show uncovered acquisition positions/tiles by name/index and the number affected.
  Preview the final expanded XY coordinates, using the same expansion as `value()`.
- OK commits valid edits atomically; Cancel discards changes. Dialog edits cannot
  change an in-progress acquisition snapshot. Opening or accepting the dialog does
  not automatically check the Surface Focus card.

Use `qtpy` imports, existing icon/button conventions, standard layouts, accessible
labels/tooltips, and no new `setStyleSheet` calls. Account for the extra card in
both tabbed and collapsible layouts (`MDAWidget` and `MDAWidgetCollapsible`, and
pymmcore-gui's subclasses), and in the available height of the settings column.

## 5. Surface model and coverage rules

Create a Qt-independent module, tentatively `src/pymmcore_widgets/_z_surface.py`,
with a validated serializable definition and a separate compiled interpolator.

```text
SurfacePoint: name?, x_um, y_um, z_um
ZSurfaceDefinition:
    schema_version = 1
    points: list[SurfacePoint]
    method: "linear" | "plane"
    outside_hull: "error"
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
6. Default coverage is the reference points' convex hull for **both** methods.
   Include boundary points within a documented numerical tolerance. Even though
   a plane equation is evaluable everywhere, refuse unsampled extrapolation.
7. An interior location can still be poorly supported. Show large triangle spans
   and nearest-reference distance; consider a user-set maximum support span after
   initial measurement trials. Do not invent a universal distance cutoff.
8. A hull can bridge gaps, holes, and distinct wells. A single surface is suitable
   only for one physically coherent region. Native per-well surfaces/masks are
   future work; nearest XY alone does not identify the correct sample region.
9. Device/configuration changes mark the map as requiring review before reuse.
   Stage re-zeroing, sample replacement, or changed optics can invalidate it even
   if device labels match. Provide a clear way to reset/revalidate its context.

Recommended dependency choice: add SciPy explicitly for maintained triangulation
and interpolation, and declare NumPy directly if the new module imports it.
Choose supported minimum versions against this project's Python/OS matrix and
update `uv.lock` with `uv`; do not rely on transitive installations. Avoid writing
a triangulation implementation. If SciPy's footprint proves unacceptable, make
that a documented design revision rather than quietly changing the default to
nearest neighbour.

## 6. Sequence construction: implement the user's `value()` approach

Keep the ordinary return type and existing `run_mda(sequence)` path. There is no
surface event adapter or engine customization in this scope.

Conceptual change inside `MDAWidget.value()`:

```python
val = super().value()
replace = {}

if surface_is_active_for_selected_positions_or_grid:
    validate_surface_and_sequence_compatibility(val)
    expanded = expand_grids_to_positions(val, origin=origin_snapshot_if_needed)
    xy = require_explicit_finite_xy(expanded.stage_positions)
    zs = compiled_surface.evaluate(xy)  # raises on uncovered positions/tiles
    replace["stage_positions"] = tuple(
        pos.replace(z=float(z))
        for pos, z in zip(expanded.stage_positions, zs, strict=True)
    )
    replace["grid_plan"] = None
    replace["axis_order"] = expanded.axis_order
    # Also use expanded AF/shutter settings if their axes were remapped.
elif existing_relative_stack_without_include_z_condition:
    # Existing behavior: use current stage Z as the shared baseline.
    ...

# Existing autofocus/current-position/save metadata/channel-property handling.
# Preserve the computed stage_positions when applying subsequent replacements.
...
```

This is pseudocode, not an instruction to call hardware during interpolation.
Read the surface model from committed UI state; `value()` must not update widgets,
move stages, run autofocus, or overwrite the position table's manual Z cells.

For no stack: `event Z = surface Z + channel Z offset`.
For a relative stack: `event Z = surface Z + relative stack Z + channel Z offset`.
Preserve existing channel `do_stack=False` semantics. Let useq apply these offsets
once; do not add them into the saved reference heights or position Z yourself.

With **software autofocus**, no extra code is needed: the event Z (surface Z plus
offsets) is where the routine starts, and the pymmcore-plus engine keeps its result
as a per-position correction (`z_after - z_before`, keyed by the `p` index) that it
adds to that position's later events, including stack planes. Positions/timepoints
where autofocus does not run (e.g. AF every N timepoints) use surface Z plus that
position's last correction. Hardware autofocus is not combined with Surface Focus
(§4).

### Expand grids into ordinary positions

This is the preferred way to obtain per-tile surface following while keeping
`value()` responsible for constructing a normal acquisition sequence. For example,
a row named `Area A` with three tiles becomes `Area A / tile 0000`,
`Area A / tile 0001`, and `Area A / tile 0002`, each with its own absolute XYZ.
If the tile centers predict 10 and 15 µm, those tiles receive 10 and 15 µm rather
than sharing the parent's Z.

1. Expand only the selected spatial plans, not the full time/channel/Z event
   product. An ordinary row without a grid yields one position. A row's sub-grid
   overrides the global grid for that row, matching current useq precedence.
2. For a relative grid, add tile offsets to the parent XY. For an absolute grid,
   use the tile XY directly. With no selected parent rows, expand the global grid
   once; capture current XY once if a relative origin is needed. Reject missing
   required coordinates instead of substituting zero.
3. Resolve actual FOV size/overlap before expansion, using the same geometry as
   acquisition. Reject missing/zero calibration when required. The engine cannot
   update spacing later after the grid has been removed. Invalidate cached tiles
   after relevant pixel-size, ROI, objective, origin, or grid changes.
4. Iterate the grid in its native scan order, including snake/spiral order and
   polygon-selected tiles. Preserve duplicate locations from distinct input rows;
   deduplicating them would change acquisition intent.
5. Copy parent position properties and supported non-grid sub-sequence settings
   to each resulting position, remove its sub-grid, and remove the global grid.
   Drop an empty child sequence. Retain plate/grid row and column information
   where available, with deterministic unique tile names and explicit provenance
   mapping `new p -> original p, original g, source name` in metadata.
6. Collapse spatial axes into one `p` axis. Initially support the normal adjacent
   `p,g` order (among active axes), plus grid-only plans where `g` becomes `p`.
   For example `t,p,g,c,z` becomes `t,p,c,z`. Orders such as `p,c,g` cannot generally
   retain their visitation order by simply making parent-major tile positions;
   reject unsupported orders with a clear message instead of rearranging them.
7. Software autofocus on `g` becomes autofocus on the flattened `p`. Software
   autofocus on the original parent `p` would now run per tile if mapped naively,
   so explicitly label/choose the resulting schedule or reject that combination in
   the initial implementation. Engine corrections are keyed by `p`, so each tile
   keeps its own. (Hardware autofocus is never combined with Surface Focus.) Remap
   or reject shutter-open axis settings as well: separate parent/grid boundary
   behavior cannot always be represented by one axis.
8. Existing UI-generated child sequences are grid-only. Imported sequences with
   additional child axes/actions need explicit preservation tests before support;
   reject unsupported combinations instead of discarding their settings.
9. Saving now sees more `p` positions and no `g` dimension. Show the resulting
   position count and verify output writers, stage navigation/progress labels,
   and any downstream assumptions about mosaic/grid indices. Metadata preserves
   tile provenance but does not itself guarantee automatic mosaic reconstruction.

Keep expansion in a pure helper and test it independently from interpolation.
It creates a regular useq sequence, so the existing channel-property sequence
handling can still wrap the completed result without a surface event adapter.

A small check against the installed useq on 2026-11-04 verified the basic approach:
two parent positions with 2-by-2 relative grids became eight absolute positions.
With two channels and a three-plane relative stack, all 48 events retained XY and
visitation order, with the expected tile-specific planar surface Z plus stack and
channel offsets. This validates the common `p,g,c,z -> p,c,z` case, not all grid,
AF, shutter, writer, or settings-restoration behavior listed above.

### Validation timing and responsiveness

The application calls `value()` for more than Run. Cache fitting, vectorize
evaluation, and keep routine enablement checks independent of full sequence
construction. Cache grid expansion as well as fitting; estimate/bound expansion
size before allocating huge position lists. Avoid enumerating tiles in every
enablement slot. Avoid recursive `valueChanged -> value() -> valueChanged` paths.

An active map that becomes invalid or uncovered must not silently revert to
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

Store the map definition and checkbox state under a versioned namespace in
`MDASequence.metadata["pymmcore_widgets"]["z_surface"]`. Keep resolved numeric
`Position.z` in the ordinary sequence fields. This makes the acquisition sequence
usable by a normal useq/pymmcore-plus consumer without this UI's map evaluator.

Illustrative metadata shape (three points shown only as a format example):

```json
{
  "schema_version": 1,
  "enabled": true,
  "method": "linear",
  "outside_hull": "error",
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
- Include an unexpanded `source_plan` for editor restoration: original selected
  positions/sub-grids, global grid, axis order, AF schedule, and shutter-axis
  settings before transformation. Exclude this surface metadata when constructing
  `source_plan` to avoid recursive embedding. Keep tile-to-source provenance
  separately. The ordinary sequence fields remain the fully resolved acquisition.
- On load, reconstruct the editor from `source_plan` when present, rather than
  showing thousands of generated tiles or expanding a saved grid twice. Restore
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

- [ ] Add the serializable definition, compiled model, validation, coverage, and
  plane/linear evaluators with no Qt dependency.
- [ ] Declare numerical dependencies and regenerate the lockfile with `uv`.
- [ ] Add meaningful tests for planar reproduction, reference vertices, hull
  boundaries, duplicate/non-finite/collinear inputs, and out-of-hull errors.
- [ ] Record a reproducible method comparison using held-out points. Keep the
  distinction between synthetic checks and measured microscope accuracy.

### Phase 2: setup and position-table controls

- [ ] Add the checkable Surface Focus card under the Autofocus card (same
  `_CheckableCard` base), its compact controls, the reference points dialog,
  capture/edit actions, and surface JSON.
- [ ] Allow Surface Focus with software autofocus; make it exclusive with hardware
  autofocus (disabled Hardware option / disabled Surface Focus card, the
  per-position AF offsets, Software selected when checking Autofocus), with the
  tooltips from §4, combined with device-based enablement, and restored correctly
  after a run.
- [ ] Separate core-independent table/dialog state from core capture/device checks;
  keep MDA-level AF/Z-plan compatibility in the MDA owner.
- [ ] Implement atomic dialog Apply/Cancel behavior and cached compilation.
- [ ] Add centralized mode coordination (Autofocus, Include Z, Z plan), descriptive
  disabled tooltips, and HCS/unsupported-order restrictions. Preserve references
  across mode changes.

### Phase 3: `value()`, saving, and execution

- [ ] Insert the surface branch ahead of the current-relative-Z fallback and
  preserve existing sequence fields, channel properties, and manual table cells.
- [ ] Add a pure grid-expansion helper for relative/absolute, global/per-position,
  polygon, and grid-only cases. Remove all consumed grids from the output, handle
  geometry calibration, and map supported acquisition order/AF/shutter settings.
- [ ] Support no stack, relative stacks, and channel offsets on every expanded
  position; retain implicit current-position behavior when no spatial plan is used.
- [ ] Add metadata round-trip including original editor grids and tile provenance,
  legacy reset behavior, and resolved position export.
- [ ] Add preflight error presentation, final target-range checks where supported,
  and consistent prepared/run snapshots without breaking subclass APIs.

### Phase 4: integration validation and documentation

- [ ] Test both MDA presentations and standalone/core-connected table behavior.
- [ ] Test device reload, focus/XY removal, mode conflicts, run disable/re-enable,
  and repeated settings loads. Verify hardware autofocus and Surface Focus can
  never be active together (UI, `setValue`, and `value()`), with the expected
  tooltips, and that software autofocus + Surface Focus is allowed.
- [ ] With the demo core and a software autofocus routine, verify each position
  starts at its surface Z, the routine's correction is kept per position (and per
  tile after flattening), and later events/stack planes use surface Z plus it.
- [ ] With the demo core, verify emitted event Z for multiple positions, relative
  stacks, channel offsets, non-stacking channels, and per-tile grid following.
- [ ] Verify expansion preserves tile scan order, applicable parent properties,
  unique names, and supported non-spatial event order/count. Test grid-only plans,
  mixed gridded/plain rows, absolute sub-grids, polygon tiles, FOV changes, and
  unsupported-order rejection. Confirm no second grid expansion occurs.
- [ ] Verify the flattened `p` output with save writers and AF/shutter schedules;
  repeated load/save must restore the original editor and keep tile counts stable.
- [ ] Verify channel property events still contain properties.
- [ ] Verify `value()` leaves widget/manual Z state unchanged, is deterministic
  for fixed inputs, and raises for uncovered positions/tiles before acquisition
  starts. Test bounded handling of very large grids.
- [ ] Test JSON/YAML round-trip, map-disabled persistence, conflicting imported
  settings, unknown schema versions, and legacy files without map metadata.
- [ ] Add a minimal example and user guide explaining reference placement,
  coverage, grid-to-position expansion, and when refocusing/re-mapping is needed.
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

- **Preserving separate `p` and `g` axes:** a later event-level approach could
  retain the original grid dimension and more complex axis/scheduling semantics.
  The proposed first implementation instead flattens grids in `value()` and
  documents supported orders. Native HCS plans remain a separate extension.
- **Multiple regions/surfaces:** assign by region/well rather than allowing a
  triangulation to bridge unrelated samples.
- **Smooth fitting:** introduce a regularized thin-plate or cubic option only
  after independent focus validation, with explicit smoothing and overshoot policy.
- **Coverage extensions:** extrapolation, nearest fallback, maximum support span,
  or constant/line maps must be named user choices, not hidden fallbacks.
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
| 2026-11-06 | Put the surface controls in a checkable **Surface Focus** card under the Autofocus card (not in the position table). | One place for the focus strategy, next to Autofocus. Supersedes the table checkbox + gear. |
| 2026-11-06 | Allow Surface Focus with **software** autofocus; exclude **hardware** autofocus. | Software AF starts from the event Z, so the surface makes its search smaller, faster and more robust (engine keeps per-position corrections). Hardware AF locks to its own reference whatever the start Z (the surface would be discarded, and per-position AF offsets would conflict with it). Tooltips: "Cannot use while hardware 'Autofocus' is checked." / "Hardware autofocus cannot be used while 'Surface Focus' is checked." Revisit "surface pre-focus for hardware AF" if capture range is a problem on real plates. |
