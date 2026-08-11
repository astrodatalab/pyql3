# Freeze the Depth Plot's Y auto-range during an ROI drag

- **Date:** 2026-08-10
- **Status:** approved, not yet implemented
- **Files:** `pyql3/gui/tools/depth_plot.py`, `tests/test_depth_plot.py`

## Problem

Dragging the Depth Plot aperture across the image is sluggish, most visibly over X11. Profiled
offscreen on a synthetic cube with real Kn5 geometry (465 channels x 51 x 19), so the numbers
below are pure application cost with no display transport:

| interaction | offscreen cost |
|---|---|
| pan the image | 0.28 ms/step |
| zoom the image | 0.27 ms/step |
| **drag the aperture ROI** | **4.50 ms/step** |
| — of which the photometry | 0.29 ms (1.10 ms with an annulus) |

A drag costs about 17x a pan before a single pixel reaches the screen, and the extraction
maths is 6% of it. The profile puts 48% in `AxisItem.paint` / `generateDrawSpecs` and 21% in
`PlotCurveItem.drawLines`.

`AxisItem` caches its rendering in a `QPicture`. Every drag step changes the spectrum, which
changes the Y auto-range, which throws that cache away and regenerates every tick string and
bounding rect. Ticking **Fix Y** and re-measuring isolates the effect:

```
baseline (auto Y range)              4.50 ms/step
with 'Fix Y' checked                 2.80 ms/step     -> 37.7% faster
```

Two things measured and rejected:

- **Curve downsampling.** `setDownsampling(auto=True, mode='peak')` plus `setClipToView(True)`
  on the three curves came out 3% *slower*. 465 points does not repay the decimation.
- **The WCS mouse readout.** A plausible suspect at 0.32 ms/event, but capped at 60 Hz by the
  existing `SignalProxy`, so about 2% of one core. Not a factor.

## What this does not do

Rejected during design, deliberately, in favour of the smallest change that captures the
measured win:

- **No throttling of the recompute.** `update_plot()` keeps running on every mouse-move event,
  so the spectrum stays live while dragging. Rate-limiting it would mean touching
  `base_tool.py`, which six tools share, and would change the behaviour of 12 test call sites
  across four files.
- **No expand-only Y range.** The frozen range can be left behind if the flux jumps orders of
  magnitude — dragging from blank sky onto a bright source — and the curve will run off the
  top of the plot until release. Accepted: hard freeze, snap on release.
- **No change to the line-list labels or the legend.**
- **`cuts.py` is out of scope.** It has the same structure — `sigRegionChanged` connected
  straight to a plot update with auto-ranging axes — and would benefit identically. Recorded
  here so it is not lost; not part of this change.

## Design

### Mechanism

The source ROI and the background ROI each gain two connections alongside the existing
`sigRegionChanged`:

- `sigRegionChangeStarted` → record whether Y auto-range is on, and if it is, disable it.
- `sigRegionChangeFinished` → if it was on, re-enable it, which snaps the view to the final
  spectrum.

`update_plot()` is untouched. It still runs on every mouse-move event and still redraws all
three curves. The only thing that stops happening is the Y-range change, and with it the
`AxisItem` cache invalidation.

Both ROI move paths emit these: `ROI._moveStarted` for a body drag and `ROI.handleMoveStarted`
for a scale handle, so resizing the aperture is covered as well as moving it.

### State

One attribute, `self._y_autorange_before_drag`. `None` means no drag is in progress.

### Read the ViewBox, not the checkbox

The freeze records `plot_widget.getViewBox().autoRangeEnabled()[1]`, not
`chk_fix_y.isChecked()`. The two can disagree: zooming the Y axis with the scroll wheel makes
pyqtgraph disable auto-range itself without touching the checkbox, so restoring from the
checkbox would silently discard a manual zoom at the end of every drag.

This also means **Fix Y needs no special handling**. With it ticked, auto-range is already
off, the freeze records `False`, and the thaw does nothing.

### Edge cases

**Programmatic moves.** `roi.setPos()` emits `sigRegionChangeFinished` but not
`sigRegionChangeStarted`. The thaw sees `None` and does nothing, so every path that moves the
ROI from code — the spin boxes, `set_center()` from the context menu, and the 12 test call
sites — behaves exactly as it does today. The change is invisible to everything except a real
mouse drag.

**Interrupted drags.** If the ROI is destroyed mid-drag, `Finished` never arrives and the Y
axis stays frozen for the life of the dialog, which looks exactly like a bug. Two paths do
this: `toggle_roi_shape()` rebuilds the ROI when the shape switches between Circle and
Rectangle, and closing the dialog removes it.

The thaw is therefore called at the top of `DepthPlotDialog.toggle_roi_shape()` and in
`DepthPlotDialog.closeEvent()` (which already overrides the base and calls
`remove_bg_roi()`), **not** from `BaseToolDialog.remove_roi_from_viewer()`. Hooking the base
class would reach the other five ROI tools for no benefit — none of them has a Y axis to
freeze — and this change is meant to stay inside one file. Calling the thaw when no drag is
in progress is a no-op, so both call sites are unconditional.

**Not every drag path is wired.** `BaseToolDialog.custom_mouse_drag` — the "Draw Region" drag
that draws a new aperture from scratch — moves `self.roi` with `blockSignals(True)` around
`setPos`/`setSize` and calls `on_roi_changed()` directly, so neither signal this mechanism
relies on ever fires; that drag pays the full `AxisItem` regeneration cost and is left for a
future change, since fixing it means touching `base_tool.py`.

## Testing

Three tests in `tests/test_depth_plot.py`, driving the signals directly. No timing assertions —
they are flaky in CI, and "the Y range did not change" is the mechanism behind the speedup, so
asserting it is both stabler and more precise than asserting a duration.

1. **A drag holds the Y range, and release restores it.** Emit `sigRegionChangeStarted`, move
   the ROI several times, assert the Y range is identical across every step; emit
   `sigRegionChangeFinished`, assert it has snapped to fit the final spectrum.
2. **A user's own Y range survives a drag.** Once with **Fix Y** ticked, once with a manual
   `setYRange` and no checkbox, assert a full drag cycle leaves the range exactly as set.
3. **An interrupted drag does not leave the axis frozen.** Start a drag, destroy the ROI via
   `toggle_roi_shape()`, assert Y auto-range is enabled again.

## Risks

Low. Nothing here touches the extraction maths, the coordinate transforms, the data-state
rules, or anything else in the CRITICAL sections of `AGENTS.md`. The change is confined to one
tool dialog's interaction plumbing, and the behaviour of every non-drag path is unchanged by
construction.

The one behavioural regression a user could notice is the accepted one: during a drag the
curve can leave the visible Y range and only settle on release.
