# HCS Cover Area mode: implementation plan

Status: proposal; no feature code implemented. Updated: 2026-11-04.

## 1. Intended behavior

Add a **Cover Area** mode immediately after **Random Points** in the HCS wizard's
Field of View Selection page. Generate a deterministic arrangement of rectangular
camera FOVs covering the selected area, with optional overlap. The area can be a
rectangle/square or an ellipse/circle and can cover either the full well or a
smaller centered restriction area.

The mode order becomes:

1. Single FOV
2. Random Points
3. **Cover Area**
4. Fields of View in a Grid

Keep the existing grid mode: it lets users specify rows and columns. Cover Area
instead derives tile count from area dimensions, shape, FOV size, and overlap.
It has no requested point count and no Randomize button.

## 2. Recommended ownership: schema plus widgets

**Recommendation:** implement deterministic geometry and a serializable relative
point plan in `useq-schema` (in the cite branch, there is a local copy of it), then implement the selector and visualization in
`pymmcore-widgets`. Tentative schema name: `GridFromShape`; tentative widget name:
`AreaCoverageWidget`. Names can change during implementation.

Why this belongs in useq:

- It describes acquisition coordinates independently of Qt or the HCS wizard.
- A script should be able to request the same rectangular or elliptical coverage
  as the GUI and get the same offsets, scan order, and tile count.
- `WellPlatePlan.well_points_plan` needs to retain the plan's identity and settings
  through JSON serialization and reload. A widget-only mode converted to ordinary
  `GridRowsColumns` cannot represent an ellipse's filtered tiles or restore the
  area controls reliably.
- This checkout's `RelativeMultiPointPlan` is an explicit union. Adding a local
  subclass in the widgets repository does not establish support in the schema's
  nested validation/serialization paths.
- Existing `GridFromPolygon` has related geometry but generates **absolute**
  positions. HCS needs **relative** offsets around each well. Passing an absolute
  polygon plan as the well's relative plan is not a compatible substitution.

Do not move HCS controls, Qt drawing, microscope calibration reads, or mode ordering
into useq. The schema receives explicit geometry/FOV values; the widget supplies
well context and device-derived FOV dimensions.

Deliver the schema work first, update the widgets dependency/lockfile to the
revision containing it, then add the GUI. This document does not authorize edits
to another repository during the planning task.

## 3. Existing integration points

Paths are relative to this repository unless identified as dependency files.
These findings describe the inspected checkout/environment, not every release.

| File / component | Current behavior and planned change |
| --- | --- |
| `src/pymmcore_widgets/useq_widgets/points_plans/_points_plan_selector.py` | Creates three mode cards, maps buttons to plan types, dispatches `setValue()`, and applies shared FOV dimensions. Add the new card between random and grid and extend every mapping/type annotation. |
| `src/pymmcore_widgets/useq_widgets/points_plans/_random_points_widget.py` | Owns random-only width/height, shape, count, traversal, and overlap controls. Reuse interaction conventions, but do not reuse its random point generator or center-domain semantics. |
| `src/pymmcore_widgets/useq_widgets/points_plans/_points_plan_widget.py` | Combines selector and preview, exposes child widgets, and handles random-only click/start-point behavior. Add the new widget alias; keep random-only actions scoped to Random Points. |
| `src/pymmcore_widgets/useq_widgets/points_plans/_well_graphics_view.py` | Shows restriction outlines only for `RandomPoints`, then greedily renders every FOV. Add area-plan outlines and scalable preview rendering without changing plan contents. |
| `src/pymmcore_widgets/hcs/_hcs_wizard.py` | Builds `WellPlatePlan`, saves/loads JSON, and synchronizes random bounds with the plate using FOV-dependent heuristics. Add explicit coverage-area synchronization instead of copying those heuristics. |
| `src/pymmcore_widgets/useq_widgets/points_plans/__init__.py` | Export the new widget if consistent with the existing points-plan public API. |
| Installed `useq/_grid.py` | Defines relative/absolute plan unions, grid scan orders, overlap spacing, and `GridFromPolygon`. Add the relative shape plan and share geometry helpers where their semantics match. |
| Installed `useq/_plate.py` | Validates `well_points_plan`, calculates counts, and adds per-well offsets to well centers. Ensure the new plan is accepted and retains tile/plate information. |

The wizard currently listens for pixel-size and configuration changes when
updating FOV dimensions. Coverage must also respond to ROI/camera changes that
alter image width/height. HCS disables manual edits of the shared FOV fields;
standalone points-plan widgets can continue to accept manual calibrated FOV sizes.

## 4. Define “restriction” and full coverage precisely

The restriction defines the **area to cover**, not a requirement that all FOVs fit
inside it. Rectangular FOVs crossing its boundary are allowed and needed for full
coverage. Some centers can also lie outside the target if their footprints cover
part of it. Explain this beside the controls:

> Cover the entire selected area. Edge FOVs can extend outside the area.

This differs from random-point placement, so reuse the area controls' appearance,
not an undocumented interpretation of their existing bounds.

| Setting | Meaning |
| --- | --- |
| Restrict Area unchecked | Cover the full well using its actual dimensions and shape. |
| Restrict Area checked | Cover the specified centered rectangle/square or ellipse/circle. |
| Width / Height | Physical dimensions of the target region in µm, not bounds on FOV centers. |
| Overlap X / Y | Requested fractional overlap of neighboring lattice FOVs, displayed as percentages. |
| Scan Order | Deterministic grid order, initially the existing row/column and snake options. |
| FOV count | Read-only result, per well and total across selected wells. |

Use the schema's existing rectangle/ellipse shape representation: equal dimensions
produce a square/circle. No separate mathematical implementations are needed.
When Restrict Area is off, disable its editable shape/size controls and show the
effective full-well geometry. Restore the user's restricted geometry when toggled
back on. Changing selected wells on the same plate must not reset the settings.

The restricted region should fit inside the well. Validate the **region's shape**,
not just its width/height: a rectangle as wide as a circular well does not fit
inside that circle. Offer an actionable error rather than silently shrinking the
region. For centered axis-aligned rectangles in an elliptical well, test corners
against the well ellipse. For other supported shape pairs, use the corresponding
containment test. Do not copy the current random-area `/1.4` approximations.

Even when the target region fits in the well, edge FOV footprints can cross the
well boundary. If a later use case requires every FOV entirely inside the well,
that needs a separately named placement policy and must acknowledge an uncovered
boundary region. Do not silently add that filter to Cover Area.

## 5. Geometry and generation algorithm

Use a regular rectangular lattice covering the target's bounding box, then retain
tiles whose **footprints intersect** the target. Filtering only by FOV centers or
requiring whole-footprint containment can leave parts of the target uncovered.

For FOV size `(fw, fh)` and overlap percentages `(ox, oy)`:

```text
sx = fw * (1 - ox / 100)
sy = fh * (1 - oy / 100)

nx = max(1, ceil(max(0, target_width  - fw) / sx) + 1)
ny = max(1, ceil(max(0, target_height - fh) / sy) + 1)

x[col] = (col - (nx - 1) / 2) * sx
y[row] = ((ny - 1) / 2 - row) * sy
```

These centered positions span at least the target bounding box. Honor requested
overlap exactly; extra extent at the outer edges is allowed. Avoid adding an
extra row/column through floating-point noise near exact divisibility. A region
smaller than one FOV produces one centered tile. This is predictable coverage,
not a claim of globally minimizing tile count or stage travel.

- **Rectangle/square:** the bounding-box lattice already covers the region.
- **Ellipse/circle:** discard non-intersecting rectangular footprints. For an
  axis-aligned ellipse centered at zero with radii `(a, b)`, the nearest footprint
  point to its center has coordinates
  `qx = max(abs(cx) - fw/2, 0)`, `qy = max(abs(cy) - fh/2, 0)`.
  The footprint intersects when `(qx/a)^2 + (qy/b)^2 <= 1`, with a documented
  numerical tolerance. Preserve boundary coverage; harmless tangent-only tiles
  can be retained consistently rather than risking gaps.
- Apply scan ordering to lattice row/column indices and filter while retaining
  that order. Default to row-wise snake. Produce stable names and `grid_row` /
  `grid_col` metadata, including skipped cells in elliptical regions.
- Expose one implementation used by iteration, counting, and preview. Do not let
  the preview independently regenerate a different layout.

The relevant distinction is supported by Shapely's documented predicates:
[intersects](https://shapely.readthedocs.io/en/stable/reference/shapely.intersects.html)
tests shared space, while [covers](https://shapely.readthedocs.io/en/stable/reference/shapely.covers.html)
tests containment. Shapely is already a dependency, so its polygon operations are
available where useful. Exact axis-aligned ellipse/rectangle intersection avoids
approximating the ellipse just to decide which tiles to acquire.

If drawing/testing an ellipse as a polygon, account for approximation error.
[Shapely buffer](https://shapely.readthedocs.io/en/stable/reference/shapely.buffer.html)
uses polygonal approximations for circular arcs. An inscribed approximation alone
does not prove coverage of the true smooth boundary. Prefer analytic selection;
use conservative approximations for generalized/rotated region support.

Coordinate and orientation requirements:

- Store relative positions and FOV dimensions in µm. Convert well sizes from mm
  once at the widget/schema boundary. Invert Y only for screen drawing.
- Verify plate rotation conventions before claiming coverage of rotated square
  or elliptical wells. The current plate code rotates well-center placement and
  adds point offsets separately; it does not establish that FOV footprints rotate
  with the plate. Keep footprints in the actual camera/stage frame and transform
  the target region appropriately, or explicitly restrict unsupported rotations.
- Invalid/missing/zero/non-finite FOV dimensions cannot produce a valid coverage
  plan. Show the missing calibration state and disable Finish; never substitute
  a nominal 1 µm FOV or silently fall back to Single FOV.
- Require positive finite region sizes and `0 <= overlap < 100` independently
  on each axis. Guard excessive candidate counts before allocating tiles.

## 6. Proposed schema contract

The core plan should contain only explicit acquisition geometry, for example:

```text
GridFromShape:
    shape: rectangle | ellipse
    width: positive µm
    height: positive µm
    fov_width, fov_height: positive µm when evaluated
    overlap: (x_percent, y_percent)
    mode: supported OrderMode
    is_relative: True
    num_positions() -> exact retained tile count
    __iter__() -> deterministic RelativePosition offsets
```

Use established schema naming conventions when finalizing field names. The HCS
widget resolves full-well versus restricted geometry before creating this plan.
Keep its **Restrict Area** toggle and cached inactive restriction settings in
explicit editor-state fields supported by the chosen save format; do not infer
the toggle just by comparing dimensions with the current plate.

Schema changes must include:

- Public export and `RelativeMultiPointPlan` / `MultiPointPlan` union membership.
- `WellPlatePlan.well_points_plan`, nested `MDASequence.grid_plan`, iteration,
  count, generated JSON schema, and JSON/YAML round-trip coverage where supported.
- Unambiguous model identification: this plan must not reload as `GridWidthHeight`
  or `GridRowsColumns` because their fields overlap. Follow the project's union
  conventions and use a discriminator if needed; test validation of dictionaries.
- Repeated iteration/count consistency, field validation, and stable scan order.
- Documentation and an example using the same plan from a script without Qt.

Prefer sharing pure tiling helpers with `GridFromPolygon` over converting this
relative plan into an absolute one. Do not modify random sampling behavior as a
side effect. A generic relative explicit-points plan could be an alternative
schema design, but it would still need separate area parameters for editing and
regeneration; the named shape plan is the recommended initial design.

## 7. Wizard integration, persistence, and preview

Add the card, value dispatch, and active-plan mappings in the selector. The shared
FOV controls remain the source of camera footprint dimensions for every mode.
Synchronize well geometry through a clear points-widget API rather than reaching
into random-widget fields from more wizard callbacks.

The preview shows the physical well outline, the restricted target when enabled,
all selected FOV footprints, and the traversal path. Keep edge-crossing footprints
visible. Counts and Finish must use the actual full plan, even if the display is
simplified for large tile counts. Avoid rendering thousands of individual path
items synchronously during every spin-box edit; debounce/reuse geometry as needed.

The fourth mode adds vertical space. Keep all controls and the shared FOV row
reachable at the supported minimum window size, using a scrollable selector area
if necessary. Preserve accessible labels, keyboard focus, palette-based colors,
`qtpy` imports, and the project convention against new `setStyleSheet` calls.

For save/load:

- Save the actual shape plan in `WellPlatePlan.well_points_plan` so headless use
  and ordinary schema round-trip preserve the generated layout.
- Decide an explicit versioned location for the small HCS-only editor state
  (restriction on/off and cached restriction geometry). Current wizard saving is
  a direct `WellPlatePlan` JSON dump; do not assume it accepts arbitrary metadata.
  Prefer a schema-supported optional editor metadata field or a backward-compatible
  wizard envelope with a loader for existing bare `WellPlatePlan` files. Record
  that API decision before implementation; do not attach undeclared attributes.
- Legacy Single FOV, Random Points, and row/column grid files retain their mode.
  New files restore Cover Area, its region/overlap/order, and restriction state.
- On actual plate changes, update full-well geometry and revalidate restrictions.
  On well-selection-only changes, preserve user input. On FOV changes, recompute
  count/layout, preserving area dimensions and overlap.
- Make preview and execution use the same resolved FOV size. The engine can
  update grid FOV sizes, so verify its behavior with the new plan and avoid a
  saved preview that no longer matches acquisition after an ROI/objective change.

HCS already expands well-point offsets into image positions. This feature does
not depend on the Z-surface plan. Its resulting XY positions can later receive
surface Z using that plan's ordinary `value()` approach.

## 8. Implementation and validation checklist

### A. Schema implementation

- [ ] Finalize plan name, field/union representation, and boundary policy.
- [ ] Implement centered lattice generation, analytic ellipse filtering, ordered
  relative positions, stable tile metadata, and exact counting.
- [ ] Add numeric validation and excessive-grid protection without partial output.
- [ ] Add public exports, schema/plate/MDA integration, serialization tests, and a
  headless example. Release or pin the supporting schema revision.

### B. Widgets and HCS integration

- [ ] Add `AreaCoverageWidget` with area restriction, shape/dimensions, overlap,
  scan order, and read-only count. Insert it immediately after Random Points.
- [ ] Extend selector type hints, button mappings, `value()` / `setValue()`, public
  aliases/exports, and points-widget documentation.
- [ ] Generalize target-outline preview support without triggering random-only
  point-count or start-point callbacks for this deterministic mode.
- [ ] Synchronize full-well geometry and FOV calibration, including ROI changes;
  validate restrictions by shape and avoid existing random-only shrink heuristics.
- [ ] Implement the chosen editor-state persistence path and legacy loading.
- [ ] Check layout at small window sizes and preview responsiveness for large plans.

### C. Tests and acceptance criteria

- [ ] Rectangle/square: exact-multiple and non-multiple sizes, independent overlap
  axes, one-FOV/smaller-than-FOV regions, and nonsquare FOVs.
- [ ] Circle/ellipse: verify the **union of tile footprints covers the target**,
  including the boundary; include tiles whose center is outside the target but
  footprint intersects. Compare analytic selection against independent geometry.
- [ ] Use geometric union/containment and independent sampled boundary checks,
  not only a test that repeats the tile-count formula. Document tolerances and
  polygon approximation limits in geometry tests.
- [ ] Stable counts/coordinates/order across repeated iteration and reload;
  verify snake traversal after omitted ellipse cells and distinct row/col metadata.
- [ ] Full-well and restricted cases across physical circular/rectangular wells,
  incompatible restriction shapes, plate changes, FOV/ROI changes, mm-to-µm
  conversion, and supported rotation behavior.
- [ ] Missing calibration and invalid sizes/overlap block completion clearly;
  huge counts do not hang the preview or silently truncate the acquisition.
- [ ] All four modes retain their state when switched; the new card is third.
- [ ] Wizard save/load restores editable Cover Area controls and generated
  positions; old JSON files and existing random/grid modes remain compatible.
- [ ] Verify `WellPlatePlan` stage XY, count, names, per-well indices, and MDA
  execution with a demo core; cover a selected subset of wells.
- [ ] Run the schema repository's required checks for its change, then
  `uv run prek -a && uv run pytest` in this repository. Require passing CI.

Existing widget tests to extend: `tests/useq_widgets/test_useq_points_plans.py`,
`tests/hcs/test_hcs_wizard.py`, and HCS position integration in
`tests/test_useq_core_widgets.py`. Add focused geometry tests in useq and a new
coverage-widget test module here as appropriate.

## 9. Decisions and scope

| Decision | Proposal |
| --- | --- |
| UI label and placement | Cover Area, immediately after Random Points. |
| Schema ownership | Relative shape-coverage plan in useq-schema; GUI and well/device context here. |
| Shape support | Rectangle/ellipse; square/circle through equal dimensions. |
| Boundary behavior | Full coverage with intersecting edge FOVs allowed outside the target. |
| Restriction | Centered optional smaller target; validate that it fits in the well. |
| Default order | Row-wise snake; start with supported deterministic grid orders. |
| Tile count | Derived, deterministic, not user-entered. |
| Persistence decision still needed | Schema-supported editor metadata versus a versioned wizard envelope with legacy loading. |
| Outside initial scope | Arbitrary drawn polygons, exclusion holes, tile-count optimization, automatic autofocus, and a fully-contained-FOV placement mode. |
