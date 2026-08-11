# Depth Plot drag Y-freeze — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the Depth Plot's Y auto-range from changing while an ROI is being dragged, so the `AxisItem` picture cache survives the drag — a measured 37.7% cut in per-event cost.

**Architecture:** Two small methods on `DepthPlotDialog` connected to `sigRegionChangeStarted` / `sigRegionChangeFinished` on the source and background ROIs, plus a thaw call on the two paths that can destroy an ROI mid-drag. `update_plot()` is not touched; the spectrum stays live during the drag.

**Tech Stack:** PySide6, pyqtgraph, pytest. Everything runs through `uv run`.

**Design doc:** `specs/2026-08-10-depth-plot-drag-y-freeze-design.md`

## Global Constraints

- Every Python invocation goes through `uv run` — nothing is installed globally.
- Run tests with `QT_QPA_PLATFORM=offscreen` to keep windows from appearing.
- **Commit messages carry no AI attribution** — no `Co-Authored-By`, no "Generated with".
- Changes are confined to `pyql3/gui/tools/depth_plot.py` and `tests/test_depth_plot.py`. **Do not modify `pyql3/gui/tools/base_tool.py`** — five other tools share it and none of them has a Y axis to freeze.
- `pg` is already imported in `depth_plot.py`; use `pg.ViewBox.YAxis` for the axis constant, matching `toggle_fix_y()` at `depth_plot.py:595`.
- Read auto-range state from `plot_widget.getViewBox().autoRangeEnabled()[1]`, never from `chk_fix_y.isChecked()`. A scroll-wheel Y zoom disables auto-range without touching the checkbox.

---

### Task 0: Branch

- [ ] **Step 1: Branch off main**

The repository is currently on `main`.

```bash
git checkout -b depth-plot-drag-y-freeze
```

---

### Task 1: Freeze the Y range for the duration of a source-ROI drag

**Files:**
- Modify: `pyql3/gui/tools/depth_plot.py` (add two methods + one wiring helper; call sites at `:459-464`, `:467`, `:1315`)
- Test: `tests/test_depth_plot.py`

**Interfaces:**
- Produces: `DepthPlotDialog._freeze_y_for_drag()`, `DepthPlotDialog._thaw_y_after_drag()`, `DepthPlotDialog._wire_drag_freeze(roi)`, and the attribute `DepthPlotDialog._y_autorange_before_drag` (`None` when no drag is in progress, otherwise the `bool` that auto-range had when the drag began). Task 2 calls `_thaw_y_after_drag()` and `_wire_drag_freeze()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_depth_plot.py`. Note `setPos(..., finish=False)`: that is exactly what pyqtgraph's own drag handler does (`ROI.py:1599`), so a real drag emits `sigRegionChanged` repeatedly and `sigRegionChangeFinished` only on release.

```python
def _drag(dialog, roi, positions):
    """Emulate a mouse drag of `roi`, the way pyqtgraph's drag handler does it."""
    roi.sigRegionChangeStarted.emit(roi)
    for pos in positions:
        roi.setPos(list(pos), finish=False)
    roi.sigRegionChangeFinished.emit(roi)


def test_dragging_the_aperture_holds_the_y_range_until_release(loaded_viewer):
    """The AxisItem caches its rendering in a QPicture that a Y-range change throws away,
    so re-ranging on every mouse-move event made a drag cost 37.7% more than it needed to.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        vb = dialog.plot_widget.getViewBox()
        assert vb.autoRangeEnabled()[1], "the Y axis should auto-range before any drag"

        dialog.roi.sigRegionChangeStarted.emit(dialog.roi)
        frozen = list(vb.viewRange()[1])

        for pos in ((5, 5), (12, 18), (25, 9)):
            dialog.roi.setPos(list(pos), finish=False)
            assert list(vb.viewRange()[1]) == frozen, \
                f"the Y range moved to {vb.viewRange()[1]} mid-drag"

        dialog.roi.sigRegionChangeFinished.emit(dialog.roi)
        assert vb.autoRangeEnabled()[1], "releasing the drag must restore auto-range"
    finally:
        dialog.close()


def test_a_fixed_y_range_is_not_re_enabled_by_a_drag(loaded_viewer):
    """`Fix Y` means fixed. The thaw restores what auto-range was, not what it might be."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.plot_widget.setYRange(0.0, 5.0, padding=0)
        dialog.chk_fix_y.setChecked(True)
        vb = dialog.plot_widget.getViewBox()

        _drag(dialog, dialog.roi, [(5, 5), (12, 18)])

        assert not vb.autoRangeEnabled()[1], "a drag re-enabled auto-range over 'Fix Y'"
        assert list(vb.viewRange()[1]) == [0.0, 5.0]
    finally:
        dialog.close()


def test_a_manual_y_zoom_survives_a_drag(loaded_viewer):
    """Zooming the Y axis with the wheel disables auto-range without ticking `Fix Y`, so
    restoring from the checkbox rather than the view would discard the user's zoom.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        vb = dialog.plot_widget.getViewBox()
        vb.setYRange(1.0, 9.0, padding=0)          # as a wheel zoom does
        assert not vb.autoRangeEnabled()[1]
        assert not dialog.chk_fix_y.isChecked(), "this test is about the two disagreeing"

        _drag(dialog, dialog.roi, [(5, 5), (12, 18)])

        assert not vb.autoRangeEnabled()[1], "the drag threw away a manual Y zoom"
        assert list(vb.viewRange()[1]) == [1.0, 9.0]
    finally:
        dialog.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_depth_plot.py -q -k "y_range or y_zoom"
```

Expected: `test_dragging_the_aperture_holds_the_y_range_until_release` FAILS on `the Y range moved to [...] mid-drag`, because nothing disables auto-range. The other two PASS already — they are regression guards for the behaviour Step 3 must not break, not red tests.

- [ ] **Step 3: Add the attribute**

In `DepthPlotDialog.__init__`, in the existing block commented `# Must exist before the first update_plot(), which reads them` (`depth_plot.py:459-464`), add a line beside `self._sky_radii_customised = False`:

```python
        #: The Y auto-range state from before the ROI drag now in progress, or None when no
        #: drag is running. Set by `_freeze_y_for_drag`, consumed by `_thaw_y_after_drag`.
        self._y_autorange_before_drag = None
```

- [ ] **Step 4: Add the freeze/thaw pair and the wiring helper**

Put these next to `toggle_fix_y()` (`depth_plot.py:595`), which is the other place the Y auto-range state is manipulated:

```python
    def _wire_drag_freeze(self, roi):
        """Hold the Y range still while `roi` is dragged.

        `sigRegionChanged` fires on every mouse-move event of a drag, and each one changes
        the spectrum, the Y auto-range, and therefore the `AxisItem` QPicture cache -- which
        is regenerated tick string by tick string. Measured at 37.7% of the cost of a drag
        step, against 6% for the photometry the drag is actually for.
        """
        roi.sigRegionChangeStarted.connect(self._freeze_y_for_drag)
        roi.sigRegionChangeFinished.connect(self._thaw_y_after_drag)

    def _freeze_y_for_drag(self):
        """Stop the Y axis re-ranging until the drag finishes."""
        if self._y_autorange_before_drag is not None:
            return  # already frozen; a second ROI, or a re-entrant signal
        enabled = bool(self.plot_widget.getViewBox().autoRangeEnabled()[1])
        self._y_autorange_before_drag = enabled
        if enabled:
            self.plot_widget.disableAutoRange(axis=pg.ViewBox.YAxis)

    def _thaw_y_after_drag(self):
        """Restore whatever the Y axis was doing before the drag, and let it snap.

        Read from the view rather than from `chk_fix_y`: a wheel zoom on the Y axis makes
        pyqtgraph disable auto-range without touching the checkbox, so restoring from the
        checkbox would silently discard the user's zoom at the end of every drag.

        A no-op when no drag is in progress, which is what makes it safe to call from the
        ROI-teardown paths and what keeps a programmatic `setPos()` -- which emits
        `sigRegionChangeFinished` but never `sigRegionChangeStarted` -- behaving as before.
        """
        was_enabled = self._y_autorange_before_drag
        self._y_autorange_before_drag = None
        if was_enabled:
            self.plot_widget.enableAutoRange(axis=pg.ViewBox.YAxis)
```

- [ ] **Step 5: Wire the source ROI at both places it is built**

`depth_plot.py:467`, in `__init__`:

```python
        self.add_roi_to_viewer(self._build_roi([center_x - r, center_y - r], [r * 2, r * 2]))
        self._wire_drag_freeze(self.roi)
```

`depth_plot.py:1315`, in `toggle_roi_shape()`:

```python
        self.add_roi_to_viewer(roi)
        self._wire_drag_freeze(self.roi)
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_depth_plot.py -q -k "y_range or y_zoom"
```

Expected: 3 passed.

- [ ] **Step 7: Run the whole Depth Plot file**

```bash
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_depth_plot.py -q
```

Expected: all pass. If a pre-existing test fails, the likely cause is a test that moves the ROI and expects a re-ranged view — read it before changing anything.

- [ ] **Step 8: Commit**

```bash
git add pyql3/gui/tools/depth_plot.py tests/test_depth_plot.py
git commit -m "hold the depth plot's Y range still during an ROI drag"
```

---

### Task 2: Cover the background ROI and interrupted drags

**Files:**
- Modify: `pyql3/gui/tools/depth_plot.py` (`:901`, `:1334`, `toggle_roi_shape` at `:1301`, `closeEvent` at `:999`)
- Test: `tests/test_depth_plot.py`

**Interfaces:**
- Consumes: `_wire_drag_freeze(roi)` and `_thaw_y_after_drag()` from Task 1.

- [ ] **Step 1: Write the failing tests**

Both are genuinely red only because Task 1 is in place — before it, auto-range was never disabled and each would pass trivially.

```python
def test_dragging_the_background_region_also_holds_the_y_range(loaded_viewer):
    """The background region is dragged exactly as the aperture is, and costs the same."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        dialog.combo_bg_mode.setCurrentText("Region")
        assert dialog.bg_roi is not None

        vb = dialog.plot_widget.getViewBox()
        dialog.bg_roi.sigRegionChangeStarted.emit(dialog.bg_roi)
        frozen = list(vb.viewRange()[1])

        for pos in ((2, 2), (8, 14)):
            dialog.bg_roi.setPos(list(pos), finish=False)
            assert list(vb.viewRange()[1]) == frozen

        dialog.bg_roi.sigRegionChangeFinished.emit(dialog.bg_roi)
        assert vb.autoRangeEnabled()[1]
    finally:
        dialog.close()


def test_an_interrupted_drag_does_not_leave_the_y_axis_frozen(loaded_viewer):
    """Switching shape destroys the ROI mid-drag, so `sigRegionChangeFinished` never
    arrives. Without a thaw on that path the Y axis stays frozen for the life of the
    dialog, which reads as a bug rather than as a fast drag.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        vb = dialog.plot_widget.getViewBox()
        dialog.roi.sigRegionChangeStarted.emit(dialog.roi)
        assert not vb.autoRangeEnabled()[1], "precondition: the drag froze the axis"

        dialog.combo_shape.setCurrentText("Rectangle")   # fires toggle_roi_shape()

        assert vb.autoRangeEnabled()[1], "the Y axis is still frozen after the ROI vanished"
        assert dialog._y_autorange_before_drag is None
    finally:
        dialog.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_depth_plot.py -q -k "background_region_also or interrupted_drag"
```

Expected: both FAIL — the first because the background ROI is not wired, the second because nothing thaws when the ROI is destroyed.

- [ ] **Step 3: Wire the background ROI at both places it is built**

`depth_plot.py:901`, at the end of the background-ROI builder:

```python
        self.bg_roi.sigRegionChanged.connect(self.on_bg_roi_changed)
        self._wire_drag_freeze(self.bg_roi)
        self.on_bg_roi_changed()
```

`depth_plot.py:1334`, in `toggle_roi_shape()`:

```python
            self.bg_roi.sigRegionChanged.connect(self.on_bg_roi_changed)
            self._wire_drag_freeze(self.bg_roi)
```

- [ ] **Step 4: Thaw on the two teardown paths**

At the very top of `toggle_roi_shape()` (`depth_plot.py:1301`), before anything reads the old ROI:

```python
    def toggle_roi_shape(self):
        # Both ROIs are about to be destroyed, so no sigRegionChangeFinished is coming for
        # a drag in progress. Unconditional: a no-op when nothing is frozen.
        self._thaw_y_after_drag()
        shape = self.combo_shape.currentText()
```

And in `closeEvent()` (`depth_plot.py:999`), which already overrides the base:

```python
    def closeEvent(self, event):
        self._thaw_y_after_drag()
        self.clear_line_overlays()
        self.remove_bg_roi()
        self.remove_annulus_rings()
        super().closeEvent(event)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_depth_plot.py -q -k "background_region_also or interrupted_drag"
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add pyql3/gui/tools/depth_plot.py tests/test_depth_plot.py
git commit -m "thaw the depth plot's Y range when a drag is interrupted"
```

---

### Task 3: Verify the win and record what was left out

**Files:**
- Modify: `TODO.md`

- [ ] **Step 1: Run the full suite**

```bash
QT_QPA_PLATFORM=offscreen uv run pytest -q -n auto --dist loadfile
```

Expected: 1045 passed, 6 skipped (1040 before this change, plus the 5 added here). The 6 skips are the `PYQL3_TEST_CUBE` tests.

- [ ] **Step 2: Measure the improvement**

Confirm the change does what it was made for rather than assuming it. Write this to a scratch file outside the repo and run it:

```python
import os, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.io import fits
from astropy.wcs import WCS
from pyql3.core.fits_reader import FitsReader
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.depth_plot import DepthPlotDialog

app = QApplication.instance() or QApplication([])
NRA, NDEC, NWAVE = 19, 51, 465          # real Kn5 geometry
data = np.random.normal(100.0, 10.0, (NRA, NDEC, NWAVE)).astype(np.float32)
w = WCS(naxis=3)
w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']
w.wcs.crval = [2.2, 34.0, -118.0]
w.wcs.cdelt = [0.0005, 0.0001, 0.0001]
w.wcs.crpix = [1, NDEC // 2, NRA // 2]
w.wcs.cunit = ['um', 'deg', 'deg']
h = w.to_header(); h['ITIME'] = 10.0; h['BUNIT'] = 'DN/s'
path = "/tmp/bench_kn5.fits"
fits.PrimaryHDU(data=data, header=h).writeto(path, overwrite=True)

reader = FitsReader(path)
viewer = ImageViewer(); viewer.set_data(reader.data, reader.header); viewer.show()
app.processEvents()
dlg = DepthPlotDialog(image_viewer=viewer, initial_center=(9, 25)); dlg.show()
app.processEvents()

def drag(n, frozen):
    if frozen:
        dlg.roi.sigRegionChangeStarted.emit(dlg.roi)
    for i in range(n):
        dlg.roi.setPos([float(3 + i % 5), float(10 + i % 9)], finish=False)
        app.processEvents()
    if frozen:
        dlg.roi.sigRegionChangeFinished.emit(dlg.roi)

for label, frozen in (("live auto-range (old behaviour)", False), ("frozen (new)", True)):
    drag(5, frozen)
    t0 = time.perf_counter(); drag(60, frozen); t1 = time.perf_counter()
    print(f"{label:34s} {1000 * (t1 - t0) / 60:6.2f} ms/step")
```

Expected: the frozen row roughly 35-40% lower. Baseline on the reference machine was 4.50 ms/step against 2.80 ms/step. If the gap is much smaller, the freeze is not taking effect — check that `_wire_drag_freeze` is reached for the ROI actually being dragged.

- [ ] **Step 3: Record the follow-up**

`cuts.py` has the identical structure — `sigRegionChanged` connected straight to a plot update with auto-ranging axes (`cuts.py:173,179,185`) — and was left out of this change deliberately. Add to the backlog section of `TODO.md`:

```markdown
- Depth Plot's drag Y-freeze applies to `cuts.py` too: it wires `sigRegionChanged`
  straight to `update_plot()` with auto-ranging axes, so every drag step throws away the
  AxisItem picture cache. Same fix, ~15 lines. Measured 37.7% on the Depth Plot.
```

- [ ] **Step 4: Commit**

```bash
git add TODO.md
git commit -m "note the same Y-freeze opportunity in the cuts tool"
```

---

## Self-Review

**Spec coverage.** Mechanism (Task 1 Steps 4-5), ViewBox-not-checkbox (Task 1 Step 4 plus its test), Fix Y needing no special handling (Task 1 test 2), programmatic moves unaffected (`_thaw_y_after_drag` no-op, exercised by every pre-existing `setPos` test in Step 7), interrupted drags (Task 2 Steps 1 and 4), both ROIs (Task 1 Step 5, Task 2 Step 3), all three named test cases plus two extra (`_drag` helper, background ROI), `cuts.py` recorded as out of scope (Task 3 Step 3). No gaps.

**Placeholders.** None.

**Type consistency.** `_freeze_y_for_drag`, `_thaw_y_after_drag`, `_wire_drag_freeze(roi)` and `_y_autorange_before_drag` are spelled identically in every task and in both test files' assertions.
