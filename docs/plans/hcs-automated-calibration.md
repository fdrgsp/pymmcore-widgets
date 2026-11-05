# HCS automated plate calibration: research and implementation plan

Status: experimental first implementation; hardware/data validation pending.
Updated: 2026-11-05. See section 11 for the implementation checkpoint.

## 1. Intended behavior and recommendation

Add two calibration-method tabs to the HCS wizard's Plate Calibration page:

- **Manual:** contains the current calibration widget, including its per-well
  center/edge/corner modes and Test Calibration view.
- **Automated:** acquires a brightfield overview with a low-magnification
  objective, detects well centers, and supplies those centers to the same plate
  calibration calculation used by Manual.

The user defines a physical scan area containing at least three non-collinear
wells in an L, for example A1, A2, and B1. The overview can contain more wells;
use all accepted, correctly labeled detections. After reviewing the overlay and
well labels, the user applies the calibration and continues to FOV selection.
The automated method does not require manually selecting well edges or centers.

**Recommended starting approach:** geometry-guided well-rim detection, combining
a threshold/contour baseline with constrained circular Hough proposals and robust
geometric refinement. Validate detections against the known well size, pitch,
shape, and lattice. Full segmentation of every well interior is unnecessary when
the desired measurement is its center.

This is a research-backed engineering recommendation, not a demonstrated winner
on this microscope. No representative plate images were provided or benchmarked
for this plan. Select the final default after the data study in section 9.

Initial automated support should cover **plates with circular wells and at least
two rows and two columns**. Coverslips, slides, one-well dishes, one-dimensional
strips, and unsupported well shapes continue to use Manual. Rectangular-well
plates are a later detector extension, not inherently unsuitable for automation.
Use the selected plate's geometry/capabilities rather than its name to decide
support; a plate called “384-well” is not necessarily circular in every model.

## 2. Existing code and integration constraints

These findings describe this checkout and its installed useq dependency.

| Component | Existing behavior / implication |
| --- | --- |
| `hcs/_hcs_wizard.py::_PlateCalibrationPage` | Hosts one `PlateCalibrationWidget`; completion follows `calibrationChanged(bool)`. Introduce the method tabs here or through a coordinator widget with the same external contract. |
| `hcs/_plate_calibration_widget.py::PlateCalibrationWidget` | Owns indexed well centers, fit state, calibration/test views, and the output `WellPlatePlan`. Extract shared fitting/state transitions before adding a second producer of centers. |
| `hcs/_well_calibration_widget.py::WellCalibrationWidget` | Supports direct center input and edge-derived centers. Automated centers should enter through a batch API, not simulated table clicks or per-detection selection changes. |
| `hcs/_util.py` | Contains simple least-squares circle fitting and axis-aligned rectangle-center helpers for manual points. These are not an outlier-resistant image detector. |
| `PlateCalibrationWidget._origin_spacing_rotation()` | Handles single wells, collinear row/column cases, and a full affine fit for non-collinear centers; returns A1 center, measured spacing, and rotation. Automated mode requires the non-collinear case. |
| `PlateCalibrationWidget.value()` and `HCSWizard.value()` | Export A1 center and rotation with the selected plate model. Measured spacing is currently informational: it is not written into `plate.well_spacing`. |
| Installed `useq.WellPlatePlan` | Represents translation, rotation, and plate spacing; does not preserve a general affine shear. Increasing row index uses inverted plate Y. |
| `control/_stage_explorer/_stage_explorer.py::AffineState` | Already handles camera/stage display mapping and stage-centered tiles. Its fallback and mirror handling need auditing before sharing it with measurement code. |
| `tests/hcs/` | Already exercises manual fitting, collinearity, calibration completion, and wizard round trips. Preserve those behaviors while adding automated-specific checks. |

Source paths above are under `src/pymmcore_widgets/`; test paths are relative to
the repository root.

Two consequences require explicit implementation work:

1. An affine fit can interpolate three non-collinear centers exactly, including
   three wrong ones. It has no remaining residual degrees of freedom. An excellent
   three-point fit alone cannot establish detection quality or correct labels.
2. A low affine residual does not guarantee an accurate exported plan: shear and
   measured pitch differences are discarded by the current representation.
   Validate predicted centers using the **actual returned `WellPlatePlan`**, after
   reduction to its supported parameters, including existing rounding.

For v1, retain the selected plate's nominal spacing and reject automated results
whose emitted plan exceeds the position-error budget. Report measured pitch and
non-orthogonality as diagnostics. Do not silently resize the plate or serialize a
transform the schema cannot represent. If benchmarks establish a need for measured
pitch, make updating the plate model an explicit shared manual/automated design
decision with save/load tests before enabling that behavior.

## 3. Acquisition workflow: defining an area before calibration exists

### User interaction

1. Select the plate model on the preceding page.
2. In Automated, choose the configured low-magnification objective/preset and
   brightfield preset/exposure. Support 2×, 4×, 5×, and up to 10× when available;
   do not discover objectives by parsing arbitrary preset names alone. Allow
   explicit preset selection and manual objective changes where appropriate.
3. Navigate with the existing microscope controls, focus until the well rim is
   visible, and record two opposing XY scan-area corners in stage coordinates.
   These are coarse bounds, not calibration points. Numeric bounds are also
   useful. Explain that the area must include entire rims of an L of wells plus
   margin. Preview a rectangle and its stage path before starting.
4. Supply an anchor well label and expected increasing-row/column directions,
   normally the top-left A1 neighborhood. This is a coarse identity/orientation
   hint, not a precisely positioned well center. A configured holder orientation
   can supply the directions, but it must be visible and reviewable.
5. Show physical FOV, tile count, estimated duration/storage, and a preview snap.
   Start **Acquire and Detect**, with progress and Cancel.
6. Review rims, centers, assigned labels, rejected candidates, and fit diagnostics.
   Allow exclusion/relabeling and rerun detection without reacquiring. If too few
   reliable wells remain, explain how to enlarge the scan or improve focus/light.
7. **Apply Calibration** commits the candidate atomically. Offer the existing
   Test Calibration view; visiting other tabs must not commit a draft.

An unlabeled repeated lattice cannot determine absolute well identity. A1/A2/B1
and D5/D6/E5 can have the same local appearance. Rotation/reflection can also
produce ambiguous assignments. Resolve these using the declared anchor and
orientation plus overlay review; never silently label the leftmost detection A1.
With an inconsistent hint or multiple equally plausible assignments, require
relabeling or return to Manual. OCR is not a v1 dependency.

### FOV and scan size

Nominal magnification does not establish whether a well fits in one frame. Use
the effective calibrated pixel-to-stage map, actual image dimensions, ROI, and
binning. For an illustrative axis-aligned 2048-pixel camera with 6.5 µm sensor
pixels and no relay magnification, the width is 6.66 mm at 2×, 3.33 mm at 4×,
2.66 mm at 5×, and 1.33 mm at 10×. These are calculations for that hypothetical
camera, not microscope specifications. Even at 2×, a three-well neighborhood
will usually need tiling.

For adjacent circular wells with pitch `(px, py)` and diameter `d`, an aligned
A1/A2/B1 bounding rectangle spans approximately `(px + d, py + d)` before
margin. Rotation changes its axis-aligned bounds. User-recorded physical bounds
avoid depending on a plate transform that does not exist yet. A rectangular
overview may also capture B2, which is useful redundancy; a disconnected L-shaped
scan optimization can wait.

Start with 15–20% tile overlap as a benchmark setting, not a guaranteed sufficient
value. Choose counts/path so calibrated tile footprints cover the requested area
with no holes, including camera rotation and mirrored axes. Show excessive scan
cost at 10× rather than excluding the objective purely by its name. Reuse tiling
helpers where appropriate, but do not require the proposed Cover Area feature or
a calibrated `WellPlatePlan` to perform this physical XY survey.

### Acquisition and microscope state

- Use one worker owning serialized stage moves, settling/waits, shutter/preset
  changes, and snaps. Update Qt only through signals on the GUI thread. Follow
  existing `superqt` worker conventions; import Qt through `qtpy`.
- Block starting during an MDA or conflicting camera acquisition. Coordinate
  live view using existing stop/restart behavior and prevent other controls from
  changing the hardware mid-survey. Do not start a second independent core owner.
- Capture immutable image copies with actual settled XY, Z, objective/preset,
  camera, exposure, ROI, binning, pixel calibration, transform, and timestamps.
  Preserve raw bit depth; the display LUT must not change detector inputs.
- Require a valid calibrated pixel scale **and verified camera/stage orientation**.
  Scalar pixel size alone is insufficient for a rotated or mirrored camera.
- Keep the initial workflow at a user-set rim focus. If necessary, add an explicit
  bounded Z sweep later; do not silently introduce unbounded autofocus. Focus on
  a sharp well rim can differ from focus on cells at the well bottom.
- Snapshot and restore temporary camera/illumination settings on success, error,
  and cancellation. Check cancellation between hardware operations; a blocking
  device call may need to finish first. Report device/restore failures clearly.
  Do not issue an automatic return XY/Z move after an error; offer an explicit
  return action and retain the actual current position.
- Validate travel limits and available scan bounds, estimate memory before
  allocation, and use bounded tile buffers or temporary storage for large scans.
  Closing the wizard cancels work and prevents callbacks into deleted widgets.

## 4. Research: which detection method fits this problem?

### Evidence and alternatives

| Approach | Useful properties | Risks in this application | Proposed role |
| --- | --- | --- | --- |
| Global threshold + morphology + contours | Simple, fast, interpretable; works when rim/interior contrast separates cleanly. | Illumination gradients, cells, menisci, broken or double rims can merge/split masks and bias centroids. | Required baseline; candidate generator for clean plates. |
| Local threshold + contours | Can accommodate spatially varying background. | Neighborhood size and contrast polarity matter; may emphasize cells/texture instead of the rim. | Benchmark against corrected global thresholding. |
| Canny/gradient edges + radius-constrained circular Hough | Uses expected geometry and can propose centers from incomplete edge evidence. | Bubbles and double rims also vote; large radius ranges and mosaics cost memory/time. | Primary alternative proposal generator for circular wells. |
| Robust circle fit to candidate rim points | Rejects contaminating edge points and gives geometric residuals. | A short arc can fit well but leave the center poorly determined; requires good proposals and coverage checks. | Refine proposals; do not fit all overview edges indiscriminately. |
| Normalized template / annular correlation | Uses a known rim pattern; suitable when appearance is repeatable. | Sensitive to scale, focus, material, and rim appearance; can match the wrong repeated feature. | Challenger/fallback if Hough struggles on a consistent plate family. |
| Fast radial symmetry | Gradient-based center proposals without requiring a filled mask. | Additional implementation/tuning and no demonstrated advantage for these plates. | Research challenger if Hough runtime or contrast robustness is insufficient. |
| Ellipse or rectangular-rim fitting | Supports additional shapes and diagnostic deviations. | More parameters can overfit partial boundaries; does not fix a bad coordinate transform. | Later extension after circular plates pass validation. |
| Learned segmentation, including SAM/µSAM or a custom model | Can model appearances that simple geometry misses. | Requires task-specific validation, model/runtime distribution, and potentially annotated training images. | Escalation only if classical methods miss the measured acceptance target. |

[Otsu thresholding](https://scikit-image.org/docs/stable/auto_examples/segmentation/plot_thresholding.html)
separates two intensity classes by their variance; that objective does not encode
well shape or identity. [Local Niblack/Sauvola thresholds](https://scikit-image.org/docs/stable/auto_examples/segmentation/plot_niblack_sauvola.html)
use neighborhood statistics to handle nonuniform backgrounds. Their documented
examples establish the mechanisms, not suitability for this plate dataset.

[The scikit-image Hough example](https://scikit-image.org/docs/stable/auto_examples/edges/plot_circular_elliptical_hough_transform.html)
uses edge images and plausible radii and discusses robustness to missing edge
points. [OpenCV's gradient Hough implementation](https://docs.opencv.org/4.x/d4/d70/tutorial_hough_circle.html)
first proposes centers and then estimates radii. These support a size-constrained
detector rather than an unrestricted search over every possible circle.

There is relevant experimental precedent: [Poisson statistics-mediated
particle/cell counting in microwell arrays](https://www.nature.com/articles/s41598-018-20913-0)
uses Hough detection followed by fitting a known well array. That work concerns
microfabricated microwells; it does **not** establish performance on conventional
brightfield multiwell plates. It supports combining local detection with lattice
geometry, which is the proposed approach here.

[scikit-image measurement tools](https://scikit-image.org/docs/stable/api/skimage.measure.html)
provide contours, region properties, circle/ellipse models, and RANSAC, making a
single-library prototype practical. [Chernov and Lesort](https://arxiv.org/abs/cs/0301001)
analyze circle fitting and its numerical behavior. Their work motivates checking
geometric conditioning and arc coverage, rather than trusting fit residual alone.

[Normalized template matching](https://scikit-image.org/docs/stable/auto_examples/features_detection/plot_template.html)
finds repeated image patches by normalized cross-correlation.
[Loy and Zelinsky's radial-symmetry method](https://researchportalplus.anu.edu.au/en/publications/a-fast-radial-symmetry-transform-for-detecting-points-of-interest-2/)
offers another geometry-based proposal mechanism. Benchmark either only if it
addresses a measured failure; avoid shipping a collection of unvalidated methods.

[Segment Anything for Microscopy](https://www.nature.com/articles/s41592-024-02580-4)
demonstrates microscopy adaptation and fine-tuning for its evaluated tasks.
It does not validate plate-rim localization. Our preference to begin with
classical geometry is an engineering inference from the known well dimensions
and need for measurable center accuracy, not a claim that learned methods cannot
work. Neither a cell-segmentation model nor a generic mask guarantees a correct
well center or well label.

### Proposed detector pipeline

1. **Normalize cautiously.** Convert to floating point with explicit grayscale
   handling; flag saturation, insufficient contrast, and severe blur. Apply mild
   smoothing at a scale below the rim width. Prefer a measured flat/dark field
   when available; compare an uncorrected branch if correction is questionable.
   Use physical units for filters and tolerances, converted through the analysis
   resolution. Avoid estimating a background at the same scale as the wells and
   accidentally subtracting their boundaries.
2. **Make proposals at a bounded overview resolution.** Compare thresholded
   contours against gradient/Canny plus circular Hough. Search radii near the
   plate's modeled well radius, allowing documented material/rim tolerances.
   Test both contrast polarities. Do not assume the physical outer rim diameter
   exactly equals the model's usable well diameter.
3. **Refine using original-resolution rim evidence.** Around each proposal,
   collect edges in an annulus, check approximately radial gradient directions,
   fit a circle with RANSAC, and refine inliers with a geometric-distance
   objective. Sample edges by angular sectors so a dense short arc does not
   dominate. Keep inner/outer rim hypotheses separate and compare centers.
4. **Score the measured boundary.** Record radius agreement, edge contrast,
   supported angular sectors, inlier fraction, radial residual in µm, center
   stability across preprocessing/edge subsets, and source-tile coverage.
   Reject narrow-arc or border-truncated evidence in v1 rather than extrapolating
   a confident center from a fragment. Do not interpret a Hough score as a
   calibrated probability of correctness.
5. **Merge duplicate observations.** De-duplicate concentric proposals and
   detections in overlapping tiles in physical coordinates. Weight by coverage
   and uncertainty, cap correlated evidence, and retain provenance. Multiple
   observations of one well are still one calibration well.
6. **Fit the labeled lattice.** Generate assignments consistent with the anchor,
   row/column direction hints, expected pitch, and selected plate dimensions.
   Enforce one center per well and reject implausible spacing, handedness, or
   near-collinear configurations. With additional wells, use robust lattice
   fitting to reject outliers; retain all accepted non-collinear centers.
7. **Validate the output plan.** Feed accepted indexed centers into the shared
   solver, build the real `WellPlatePlan`, and evaluate its predicted centers,
   local uncertainty, and extrapolation over the intended plate selection.
   Present all rejected points and diagnostic reasons in the overlay.

The recommended hybrid should use a small, deterministic set of proposal paths,
not an endless parameter search until exactly three circles appear. Require
agreement or clear quality margins when paths disagree. Freeze the chosen path,
thresholds, seed, and version in the result report for reproducibility.

Illumination correction deserves a controlled comparison:
[Peng et al.'s method](https://www.nature.com/articles/ncomms14836) addresses background and
shading in microscopy. Start with measured flat fields or simple validated
correction; do not infer that this method can reliably estimate illumination from only
three images dominated by repeated well structures. It is an optional study,
not an initial runtime dependency.

## 5. Tiles, coordinate transforms, and stitching

Detection accuracy depends on the image-to-stage mapping as much as segmentation.
[Micro-Manager's configuration guide](https://micro-manager.org/wiki/Micro-Manager_Configuration_Guide)
distinguishes scalar pixel size from the affine camera/stage relation.
[The MMCore API](https://micro-manager.org/apidoc/MMCore/latest/class_c_m_m_core.html)
documents that `getPixelSizeAffine()` accounts for binning and known magnification
devices and may return zeros when no calibration matches. Validate the installed
binding's behavior as part of implementation; do not apply binning twice.

Establish one tested mapping with a convention such as:

```text
stage_xy = settled_tile_xy + J @ (image_uv - reference_uv)
```

Here `image_uv` means `(column, row)` in the raw acquired array, `J` is the
verified 2×2 pixel-to-stage map in µm/pixel, and `reference_uv` corresponds to
`settled_tile_xy`. Define pixel-center, full-sensor versus ROI-center, and
translation conventions explicitly. Derive them from the acquisition path and
verify with known XY moves; do not assume the ROI center is the optical center.
Test camera rotation, transpose/mirror settings, asymmetric ROI offsets, and
binning. Convert image `(row, column)` APIs exactly once. Keep useq's plate-row
Y inversion separate from camera and screen conventions.

The existing stage viewer is a useful reference, not proof of a metrology-safe
transform. In particular, audit its identity/zero-affine handling, positional
arguments in its fallback, and mirror composition. Do not reuse a display
fallback of 1 µm/pixel or unresolved mirror behavior for automated measurements.

Build a downsampled stage-registered mosaic for proposals/review, with an explicit
valid-data mask; fit final rims using original tile pixels transformed to stage
coordinates. This allows a well larger than one FOV to contribute arcs from
multiple tiles without treating blended mosaic seams as true edges. For an
anisotropic transform, a circular stage-space rim need not be circular in raw
pixels; rectify proposals or use an appropriate pixel-space model.

Start with stage-based placement and measure seam disagreement. If repeatability
or backlash exceeds the error budget, add bounded overlap registration constrained
by stage metadata, preserving its relation to real stage coordinates. Do not
allow independent scale/shear warps that make the overview look correct while
changing physical distances. [MIST](https://www.nature.com/articles/s41598-017-04567-y)
explicitly models mechanical stage uncertainty for microscopy stitching and
discusses weak overlap information. It motivates measuring these errors, not
requiring the entire MIST application as a dependency.

Blank overlaps and repeated well patterns can make registration ambiguous.
[scikit-image phase correlation](https://scikit-image.org/docs/stable/api/skimage.registration.html#skimage.registration.phase_cross_correlation)
also has modulo-image shift ambiguity. Use overlap masks, bounded stage priors,
and consistency checks; a low-confidence match must not displace a tile by one
well pitch. Reject an untrustworthy mosaic or request reacquisition instead.

## 6. Accuracy, identifiability, and failure behavior

Three centers in an L are the minimum, not an accuracy guarantee. Prefer more
centers and longer horizontal/vertical baselines where practical. An adjacent
A1/A2/B1 group can determine pose, but small angular errors grow far from that
group: approximately `position_error = baseline_distance * angle_error_radians`.
For illustration, 0.1° gives about 175 µm at 100 mm. A dense-well plate or small
high-magnification FOV may need a much smaller error.

Set an explicit position-error budget in µm from the intended imaging use case
before choosing acceptance thresholds. As a proposed initial engineering target,
use no more than 10% of the smaller acquisition FOV dimension, capped at 100 µm;
this must be validated or replaced for the actual assay. If final acquisition
FOV is not yet known on this wizard page, show the tested absolute tolerance and
revalidate at the FOV-selection page before enabling Finish.

Acceptance requires all of the following:

- At least three distinct accepted, labeled, non-collinear wells; adequate L
  baseline/conditioning and rim coverage, not just a count of circles.
- Known absolute identity/orientation with no unresolved reflected or shifted
  lattice assignment; plausible physical well dimensions and both pitch axes.
- Stable per-well centers and an emitted-plan error below budget. Display RMS
  and maximum error in µm, measured pitch in both axes, orthogonality deviation,
  detection count, and distance over which the fit will be extrapolated.
- With more than three wells, evaluate withheld/leave-one-out predictions. For
  exactly three wells, explicitly report the absence of independent residual
  validation; use boundary evidence and uncertainty propagation rather than a
  zero affine residual as confidence.
- Estimate uncertainty using angular-sector/tile resampling and repeat scans,
  including stage/transform error. A pixel-wise bootstrap alone underestimates
  correlated errors. Reject an automated result if predicted uncertainty across
  intended wells exceeds budget; suggest a wider scan or Manual.

Offer an optional independent check at a distant predicted well after applying
the provisional pose. Any additional scan needs its own visible bounds/path and
must not expand the original survey silently. Check objective-change offsets
when returning to the acquisition objective: parfocal/parcentric behavior is not
guaranteed by magnification alone. A correct survey calibration can otherwise
produce shifted high-magnification positions.

Failure messages should identify the remedy: missing pixel transform, unsupported
well shape, insufficient rim contrast, incomplete rims, fewer than three wells,
ambiguous labels, inconsistent pitch, excessive uncertainty, or unreliable tile
placement. Never substitute nominal centers or accept fabricated wells to meet
the count. Cancel/error leaves the last applied calibration intact. An actual
plate-model change clears incompatible drafts/results and applied calibration;
changing only selected wells preserves calibration and rechecks its error extent.

## 7. Architecture, shared state, and persistence

Keep acquisition/detection in this repository initially; no new useq acquisition
plan type is needed. Suggested internal responsibilities (names tentative):

- `_plate_calibration.py`: pure indexed-center fitting/finalization and diagnostic
  types, shared by Manual and Automated. Extract existing math first with
  regression coverage; do not change manual single-well/collinear behavior as a
  side effect of imposing stricter automated requirements.
- `_automated_plate_calibration_widget.py`: Automated controls and review overlay.
- `_plate_survey.py`: physical XY tiling, hardware transaction, image metadata,
  bounded storage, progress, cancellation, and cleanup.
- `_well_detection.py`: preprocessing, proposals, robust rim fitting, duplicate
  merging, lattice assignment, and pure numerical quality checks.
- A calibration coordinator: owns the applied plan/centers and method tabs,
  emits `calibrationChanged(bool)`, and provides `value()` / `setValue()` to the
  wizard. Manual edits retain their existing live-update semantics. Automated
  draft results commit through one batch operation after validation.

Represent observations explicitly: indexed center in stage µm, fitted radius,
rim quality/uncertainty, tile IDs, accepted/excluded state, and algorithm version.
Keep this separate from the final `WellPlatePlan`. Both methods call the same
finalizer; automation must not maintain a second incompatible transform formula.
Do not merge an old manual center with a new automated survey implicitly.

Applying a batch updates the manual view in direct-center mode, including its
table/model and calibrated markers, and updates the test view once. Calling
`setWellCenter()` alone currently only updates center state/icon and emits a
signal; add a deliberate hydration API instead of assuming it fills the table.
Loading a plan with no original observations shows an applied loaded calibration
without inventing raw edge points or detector confidence.

Give every survey/detection run a generation ID and a snapshot of plate, camera,
objective, ROI, calibration, and survey inputs. Discard stale worker results if
those inputs change or the widget closes. Merely switching method tabs preserves
drafts; a failed draft cannot replace a valid applied calibration. Serialize core
access and keep heavy processing outside the GUI thread. Use palette-based
overlay colors and accessible labels; add no `setStyleSheet` calls.

Preserve the existing bare `WellPlatePlan` JSON save/load format for v1. A plan
created automatically must work headlessly and reload without the detector extra.
Survey imagery, editable detector parameters, confidence, and provenance can be
exported separately as an optional versioned report plus tile data. Do not add
undeclared fields to useq models, require a sidecar for loading an ordinary plan,
or persist an “automatically verified” claim without its evidence/context.

Prototype with scikit-image and SciPy in an isolated uv-managed environment.
If selected, expose a lazy optional dependency such as `pymmcore-widgets[hcs-auto]`
with tested supported versions and direct declarations for libraries imported.
Keep Manual available without that extra. Benchmark OpenCV as a challenger if
needed; choose one production backend initially. Do not add both OpenCV and
scikit-image, model weights, or a GPU requirement without measured benefit.
Check Python 3.11–3.14 and supported-platform wheel availability before choosing
versions; do not assume a package supported on the developer's machine covers CI.

## 8. Implementation sequence

### Phase 1: feasibility and dataset, before choosing the default

- [ ] Collect representative raw brightfield tile sets and microscope metadata;
  include 2×/4×/5×/10× where physically supported, different plate materials,
  well diameters, empty/filled wells, cells, debris, and illumination/focus changes.
- [ ] Verify image-to-stage mapping with known XY movements, rotated/mirrored
  camera cases, binning/ROI changes, and the installed core affine behavior.
- [ ] Benchmark threshold/contour and constrained Hough + robust-refinement
  pipelines using identical inputs and lattice validation; retain a simple baseline.
- [ ] Determine usable survey resolution, overlap, arc coverage, radius windows,
  runtime/memory limits, error budget, and supported plate families.
- [ ] Record a go/no-go decision and failure examples. Restrict v1 support rather
  than presenting an unvalidated detector as universal.

### Phase 2: shared calibration and tab shell

- [ ] Extract common fitting/finalization, add batch-center hydration, and verify
  emitted-plan geometry including nominal-spacing limitations and row signs.
- [ ] Add Manual/Automated method tabs and the applied/draft coordinator contract.
  Preserve the current Manual UI and Test Calibration behavior.
- [ ] Implement capability/missing-extra messages, state invalidation, and legacy
  plan loading without requiring survey evidence.

### Phase 3: acquisition and detection

- [ ] Implement stage-space bounds entry, objective/brightfield settings, preview,
  path/size estimates, and complete-rim coverage guidance.
- [ ] Implement serialized asynchronous survey acquisition, storage limits,
  hardware cleanup, cancellation, and stale-result protection.
- [ ] Implement the selected detector, original-tile refinement, provenance,
  duplicate merging, anchored lattice assignment, and uncertainty gates.
- [ ] Add rim/center/label overlays, rejected-candidate reasons, exclusion and
  relabel controls, offline rerun, atomic Apply, and shared calibration testing.

### Phase 4: validation and documentation

- [ ] Validate held-out real scans and full-plate predictions, including transfer
  back to the intended acquisition objective and small-FOV completion gating.
- [ ] Add optional evidence export, user documentation, and a demo-core example
  using synthetic plate images; clearly identify it as a simulation.
- [ ] Run `uv run prek -a && uv run pytest` for the implementation. This planning
  change itself requires document checks only; no hardware feature is added.

## 9. Benchmark and acceptance protocol

Use well-center error and calibration error as the primary metrics, not mask IoU.
Have two observers annotate full rims/centers and labels, reconcile disagreement,
and document reference uncertainty. Obtain independently measured stage centers
or a reference calibration where practical. Include distant wells not used for
fitting; evaluating only the same three points cannot validate plate-wide pose.

Split data by physical plate/run and, if available, microscope/objective setup.
Do not place overlapping tiles or crops of the same well into both tuning and
held-out sets. Keep preprocessing/radius/quality thresholds fixed for the final
comparison. Record the supported configuration envelope and all failures.

| Test group | Required observations |
| --- | --- |
| Real circular-well plates | Center error in µm, recall, false positives, incorrect labels, accepted-calibration rate, and false accepted calibrations by plate/objective/condition. |
| Adverse inputs | Empty/filled wells, weak and inverted contrast, cells, bubbles, double rims, saturation, blur, cropped rims, missing wells, and large artifacts. |
| Coverage/geometry | Exactly three L wells, additional wells, wider baselines, diagonal/row-only rejection, shifted/reflected assignments, rotation, and extrapolation to distant wells. |
| Transform/acquisition | Camera angle/flips/transpose, ROI origin, binning, invalid/zero/identity affine, missing pixel size, stage backlash, settling errors, and objective-change offsets. |
| Mosaic boundaries | Wells spanning many tiles, duplicate detections, blank overlaps, seam errors, invalid pixels, and ambiguous registration. |
| Qt/lifecycle | Progress responsiveness, Cancel/close/error cleanup, conflicting MDA/live acquisition, plate changes during work, delayed results, tab switching, and atomic replacement of a previous calibration. |
| Compatibility | Existing manual modes and small plates, loaded plans without observations, old JSON, automatic-plan round trips, and use without optional dependencies. |

For accepted runs, report median/95th-percentile/maximum center error and
independent predicted-position error against the agreed budget, plus worst
conditions and latency/peak memory. Track wrong well labels separately: even one
pitch error is unacceptable regardless of fit residual. Require zero incorrectly
accepted labels/over-budget plans in the held-out release set, report its size
and confidence limits, and do not claim this guarantees zero production errors.
Declining a difficult scan with a clear remedy is preferable to false acceptance.

Synthetic tests should vary geometry, gradients, partial arcs, clutter, noise,
and tile/transform errors with known ground truth. They are useful for numerical
regression and rare cases but cannot replace real brightfield evidence. Test the
geometry against independently generated stage positions and the actual emitted
useq plan, not a duplicate of the solver's formula.

## 10. Decisions to revisit after the feasibility study

- Which physical plates expose a consistently visible rim in transmitted light,
  and at which focus/illumination/objective settings?
- Does stage-based placement meet the error budget, or is constrained registration
  necessary? Can the current affine/display helper be shared after correction?
- Does the selected plate model's nominal diameter describe the detected rim,
  and is nominal pitch accurate enough for exported positions?
- How much baseline and how many wells are needed for each intended acquisition
  FOV, rather than merely for a mathematically determined local fit?
- Should measured pitch become a shared calibration capability? Is a bounded rim
  focus sweep necessary? Which rectangular-well plate family should be next?

The first deliverable is a reproducible comparison on representative images and
a validated coordinate mapping. The UI and detector implementation should follow
that evidence while retaining Manual as the existing calibration method.

## 11. Current implementation checkpoint

The initial experimental workflow has been simplified following UI review:

- Manual and Automated have matching left/right layouts. Automated reuses the
  manual point editor, fixed to two opposing corners, its square-corner icon,
  Set/Clear controls, and green completion tick. Start Scan sits below it.
- Automated has Select Wells, Calibrate Plate, and Test Calibration tabs. The
  first tab uses the manual plate drawing with multiple selection. Start Scan
  and Calibrate Plate enable after a valid selection and both corners. A 2 x 2
  block is recommended; 3+ wells in a single row/column and other non-collinear
  groups are supported. This includes single-row/column strips. Start Scan
  switches to Calibrate Plate.
- The image canvas reuses the Stage Explorer VisPy viewer and displays each tile
  during acquisition. Rims, centers, and labels use a separate toggleable layer.
- Detection runs only when the user clicks Detect Wells after setting parameters
  with the gear dialog. It never starts automatically after a scan. Repeating
  detection uses retained raw tiles without reacquisition. There is one detector,
  so no method selector. Settings use label-and-spin rows with fixed label widths
  based on the longest label; spin boxes align across all rows.
- The preselected wells provide identity. The detected count and measured lattice
  must match that selection before applying a calibration. There is no Identify
  Wells button/dialog and no rotation input. Normal holder orientation is assumed
  and rotation is measured from the centers. Selection changes retain scan images
  but discard detection drafts, allowing detection against a corrected selection.
- Apply Calibration feeds the reviewed centers into the shared solver. Test
  Calibration follows the same applied plan as Manual, including loaded plans.
- A row/column uses the existing manual collinear solver, now shared as a pure
  function. It measures position and rotation along the line and assumes nominal
  perpendicular spacing and orthogonal axes. Strips can have zero spacing on the
  unused axis. Test-view markers ignore that unused spacing when sizing spots.
- A configured pixel size is the imaging-geometry prerequisite. The widget uses
  it directly for scan spacing and stage placement of raw image pixels; the
  previous affine-calibration prerequisite and geometry confirmation boxes are
  removed. No pixel-size calibration routine is introduced here.

Install optional processing dependencies with `uv sync --extra hcs-auto`.
Manual and loaded plan testing remain available without this extra. The user
sets the objective, brightfield exposure/illumination, and rim focus outside the
wizard. Surveys are bounded to 256 tiles and 512 MiB of raw images, with no
automatic XY return. Raw images/settings are not persisted in plan JSON.

Software tests cover tiled rims, shared calibration, two-corner completion and
pixel-size gating, streamed tiles, manual detection, detection-only reruns,
overlay visibility, settings, shared calibration testing, stale results, and
cancellation. These establish software behavior, not microscope accuracy. There
are no real plate benchmarks yet, so the workflow remains experimental.

Remaining research/production work includes the threshold/contour comparison,
representative brightfield datasets, objective transfer and plate-wide accuracy,
constrained seam registration if needed, evidence export, and other plate shapes.
The current tolerance applies to measured calibration wells; it is not an
independent guarantee for distant wells. Earlier research sections describe the
broader design considerations; this checkpoint records the current UI and the
user's pixel-size-only prerequisite.
