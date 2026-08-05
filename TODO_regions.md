# Plan: ds9-style regions (circle, box, arrow, text)

Status: **planned, not started.** Written 2026-08-05 against commit `e2be099`.

Goal: draw circles, boxes, arrows and text on the image the way ds9 does, edit them
interactively, and save/load them — natively in a flexible format (YAML), with ds9 `.reg`
as an import/export format so regions can be exchanged with ds9.

Replaces two entries in `TODO.md`: *"Ability to parse and load ds9 region files"* and
*"Ability to draw arrows, circles, squares, and custom regions on images like ds9"*.

## Decisions already taken

| Question | Decision |
|----------|----------|
| ds9 `.reg` parsing | Use the **`regions`** package (astropy-affiliated) for the shapes it supports; hand-write arrows, which it cannot do (see below) |
| What a new region is anchored to | **Pixels, with the sky position recorded alongside** when a WCS exists. The region stays where it was drawn; the sky value is there for ds9 export and cross-frame use |
| Native format | **YAML**, versioned, `yaml.safe_load` only |
| UI placement | **New top-level `Region` menu** |

## What was verified (do not re-litigate without re-running)

Run with `uvx --with regions python -c ...` — `regions` is not a project dependency yet.

1. **`regions` 0.12 drops ds9 `vector(...)` silently.** Feeding it a file containing
   `circle`, `box`, `# vector(10,10,40,45) vector=1`, `# text(70,70) text={hello}` and
   `line` yields `['CirclePixelRegion', 'RectanglePixelRegion', 'TextPixelRegion',
   'LinePixelRegion']` and **an empty warning list**. Arrows must be hand-written on both
   read and write.
2. **`line=0 1` arrowhead flags are dropped too**, but with a warning
   (`DS9 meta "line=0 1" is unsupported and will be ignored`). So "export arrows as ds9
   lines with arrowhead flags" is not a way around item 1 — the flags do not survive.
3. **`PixCoord` is 0-based and `regions` applies the ds9 1-based shift itself.**
   `CirclePixelRegion(PixCoord(10,20), 5)` serializes to `circle(11,21,5)`. We hand it
   0-based pixels and own only pyqtgraph's extra half-pixel.
4. **`regions` writes text without ds9's leading `#`**: `text(2,3) # text={hi}` where ds9
   writes `# text(2,3) text={hi}`. Both its reader and — checked in ds9 itself — ds9 accept
   either form, so **text needs no post-processing on export**. Arrows are the opposite: a
   bare `vector(...)` is a hard parse error in ds9 that rejects the entire file, so ours must
   be written `# vector(...) vector=1`. See "ds9 format check" below.
5. **Colour arrives under different keys per shape.** `color=red` parses to
   `visual={'facecolor','edgecolor'}` on a circle but `visual={'color'}` on text. Both
   must be mapped or regions load with the wrong colour.
6. **Packaging surface:** 7 compiled `*.abi3.so` extensions under `regions/_geometry/`, no
   data files. `QuickLook3.spec` needs `collect_all('regions')`, as it already does for
   `photutils` and `cmcrameri`.
7. **`wcs.celestial` is silently wrong for OSIRIS cubes.** For the conftest WCS it returns
   `ctype == ['DEC--TAN','RA---TAN']`, so `world_to_pixel_values(ra, dec)` returns
   `(nan, nan)` — no exception, no warning. Always use
   `wcs.sub(['longitude','latitude'])`, which gives `['RA---TAN','DEC--TAN']`, or look up
   `world_axis_physical_types` as `plot_catalog._row_to_display` does.

Things already in our favour:

- View rotation is a `QTransform` on the **ImageItem** (`image_viewer.apply_view_rotation`),
  so anything parented to the ImageItem inherits it for free — this is why existing tool
  ROIs stay glued to the image.
- pyqtgraph 0.14 has every primitive needed: `CircleROI`, `RectROI` + `addRotateHandle()`
  for a position angle, `LineSegmentROI` + `ArrowItem`, `TextItem`, and `removable=True`
  for a ds9-like right-click delete.

## ds9 format check

**Settled (checked in ds9, 2026-08-05):**

- ds9 accepts **both** text forms — `# text(x,y) text={...}` and the un-prefixed
  `text(x,y) # text={...}` that `regions` emits. The text export path is therefore just
  `regions.serialize()`, with nothing to rewrite.
- `# vector(x,y,len,angle) vector=1` loads.
- **A bare `vector(...)` is a hard parse error and ds9 rejects the whole file.** Not an
  unknown-shape skip — a syntax error. `text` is a real shape keyword in ds9's grammar while
  `vector` exists only in the `#`-prefixed annotation syntax. Our arrow writer must always
  emit the prefixed form, and our reader should still tolerate the bare form, since users
  hand-edit these files.
- `line(x1,y1,x2,y2) # line=0 1` loads, and the library's 8-decimal number format loads, so
  nothing has to be reformatted on export.
- **`regions.serialize()` output loads in ds9 unmodified**, header line
  (`# Region file format: DS9 astropy/regions`) included. The ds9 export path is therefore
  `Regions.serialize()` plus appended `# vector(...)` lines, with no rewriting at all.
- **A `#` comment starting with a dash is fatal**: `# --- dashes ---` is rejected while
  `# plain comment` and `# comment with (parentheses)` both load. `-` is ds9's exclude prefix
  (`-circle(...)`), so after `#` the parser expects a shape to exclude and hits a syntax
  error. Parentheses in a comment are *fine*, which was the surprise.
  **Writer rule: never emit a comment whose first character after `#` is `-`.**
  (Test 16 contained both leading dashes and the word "dashes", and `dash` is also a ds9
  property name — files 20 and 21 separate the two, but the rule above holds either way.)
- `textangle=30` on a text region loads, so a rotated label round-trips.
- A `text={...}` value **may** contain parentheses — nothing to escape.
- A provenance comment (`# written by QuickLook 3 v0.1.0`) loads.
- The first combined check file therefore had **two** independent fatal lines: the bare
  `vector(...)` and the dash-prefixed comments.

**The format questions are closed.** Only diagnostic cases were left unrun (17, 18, 20, 21),
and none of them changes what we write. Skip 07 and 16 if the ladder is ever re-run — both are
known bad and abort anything after them.

### The resulting export recipe

`agent_tests/probes/make_quicklook3_target_reg.py` writes
`ds9_check/quicklook3_target_output.reg`, which is exactly what our exporter should produce:

```
# Region file format: DS9 astropy/regions
# written by QuickLook 3 v0.1.0
image
circle(100.00000000,100.00000000,18.00000000)
box(100.00000000,100.00000000,46.00000000,24.00000000,20.00000000)
text(100.00000000,140.00000000) # text={a label with (parens)}
# vector(60,60,45,0) vector=1 text={east}
# vector(60,60,45,90) vector=1 text={north}
```

That is: `Regions.serialize(format='ds9')` untouched, the provenance comment inserted after the
library's header line (which is why the writer splits the serialized text rather than
concatenating), and arrows appended by hand as `# vector(...) vector=1`. Reading that file back
through `regions` returns the circle, box and text and **zero** of the two arrows — the
concrete measure of what our own vector parser is responsible for.

Keep this file as the seed for the Phase 2 writer test, and add ds9's own re-written version
(**Region ➔ Save Regions**) as the reader fixture.

### How this was tested

**ds9 rejects a region file whole.** A first attempt put every experiment in one file, and ds9
answered only "unable to load region file" — no indication of which construct it disliked, and
in the end there were two. That file has been deleted; one construct per file is the only
useful shape for this test.

The ladder is generated by `agent_tests/probes/make_ds9_check_ladder.py` (that directory is
gitignored scratch; regenerate rather than looking for it in git):

```bash
uvx --with regions python agent_tests/probes/make_ds9_check_ladder.py
```

It writes `agent_tests/probes/ds9_check/` containing `check.fits` and one `.reg` per
construct. Load the FITS first — these use `image` coordinates, so a frame with data has to
exist — then load each file with **Region ➔ Load Regions**.

| File | Construct | Loads? |
|------|-----------|--------|
| `01_minimal.reg` | header + `image` + one circle | ✅ |
| `02_global.reg` | a `global` properties line | ✅ |
| `03_shapes.reg` | circle, box with angle, line | ✅ |
| `04_text_hash.reg` | `# text(...) text={...}` — ds9's own form | ✅ |
| `05_text_bare.reg` | `text(...) # text={...}` — **what `regions` writes** | ✅ |
| `06_vector_hash.reg` | `# vector(...) vector=1` — ds9's own form | ✅ |
| `07_vector_bare.reg` | `vector(...)` un-prefixed | ❌ whole file rejected |
| `08_line_arrow.reg` | `line(...) # line=0 1` arrowhead flags | ✅ |
| `09_regions_output.reg` | `regions.serialize()` verbatim, its own header included | ✅ |
| `10_regions_shapes_ds9_header.reg` | the same 8-decimal numbers under a ds9 header | ✅ |
| `11_comment_style.reg` | a chatty comment like the ones in the rejected file | ❌ whole file rejected |
| `12_text_parens.reg` | a text value containing parentheses | ✅ |
| `13_textangle.reg` | `textangle=30` | ✅ |
| `14_comment_plain.reg` | `# plain comment` | ✅ |
| `15_comment_parens.reg` | `# comment with (parentheses)` | ✅ |
| `16_comment_dashes.reg` | `# --- dashes ---` | ❌ whole file rejected |
| `19_comment_provenance.reg` | `# written by QuickLook 3 v0.1.0` | ✅ |
| `17_comment_keyword.reg` | `# comment mentioning text and vector` | not run (diagnostic) |
| `18_comment_apostrophe.reg` | `# comment with an apostrophe's quote` | not run (diagnostic) |
| `20_comment_dashes_only.reg` | `# ---` | not run (diagnostic) |
| `21_comment_dash_word.reg` | `# comment mentioning dash and dashlist` | not run (diagnostic) |

The four unrun files only sharpen the explanation of why 16 fails; the writer rule (no comment
starting with `-`) holds regardless.

For the record, `regions` reads this file back as: both texts (it is tolerant of either
form), the line without its arrowhead, and **neither vector**.

Once ds9 has re-written the file (**Region ➔ Save Regions**), keep its output as a test
fixture under `tests/` — a file ds9 itself produced is the only trustworthy fixture for the
importer.

## Phases

### Phase 0 — one canonical coordinate mapper (prerequisite, own commit)

`apply_spatial_transforms()` flips and `rot90`s the **data**, so display pixels are not FITS
pixels, and that mapping is currently written twice in inverse forms:
`plot_catalog.map_to_display()` (raw → display, `plot_catalog.py:131`) and inline arithmetic
inside `image_viewer.mouse_moved()` (display → raw, `image_viewer.py:1443-1460`). Regions
need both directions for four shape types **and for angles** — a flip negates a box's PA and
mirrors an arrow's direction, each 90° step adds 90°. A third copy is not acceptable.

New `pyql3/core/coords.py`, pure functions of `(flip, rot_angle, shape)` so they test
without Qt:

- `raw_to_display(x, y, ...)` / `display_to_raw(x, y, ...)`, mutual inverses,
- `raw_angle_to_display(deg, ...)` and its inverse — the part neither existing copy has,
- explicit helpers for the three pixel conventions in play: FITS (1-based, centre on the
  integer), numpy (0-based), pyqtgraph (corner origin, centre at `i+0.5` — the half-pixel
  `plot_catalog._row_to_display` currently makes its callers add by hand).

Then rewire `plot_catalog` and the WCS readout to call it and delete both copies. Tests:
round-trip points and angles across all 8 flip × rotation combinations, plus worked examples
pinning the FITS ↔ numpy ↔ pyqtgraph offsets.

Worth landing on its own even if the rest slips.

### Phase 1 — model + native YAML (no GUI)

`pyql3/core/regions_model.py`: dataclasses `Circle`, `Box` (with PA), `Arrow`, `Text`, with a
shared attribute block — label, colour, line width, dash, font size, tag/group, visibility —
plus two things ds9 has no room for:

- `z_range: [zmin, zmax] | None` — a region that only applies over an emission line's
  channels,
- `frame: image | sky` — whether the stored geometry is pixels or `(ra, dec)` plus angular
  sizes, so a region file survives reloading a dithered frame with a different WCS.

Geometry is stored in **0-based pixels of the raw FITS spatial axes**, never display
coordinates, with the sky position written alongside when a WCS exists. Native file is
versioned YAML (`format: pyql3-regions/1`), loaded with **`yaml.safe_load` only**.

`pyyaml` is currently only a transitive *dev* dependency (via mkdocs) — it has to move into
`[project] dependencies`. Note also that `pydantic` is a declared dependency that nothing
imports; validate by hand here rather than adopting it, and treat "use pydantic or drop it"
as a separate question.

### Phase 2 — ds9 `.reg` IO on top of `regions`

`pyql3/core/ds9_regions.py` wraps `Regions.parse` / `Regions.serialize` for circle, box,
text and line, and owns a hand-written path for arrows on both sides (finding 1).

- **Read:** parse `vector(...)` lines out of the file text ourselves, with their own colour /
  text / tag attributes, and merge them with what the library returns. Map `visual`/`meta`
  keys onto our attributes, remembering finding 5.
- **Write:** `Regions.serialize(format='ds9')` for the non-arrow regions, unmodified, then
  append arrow lines as `# vector(x,y,len,angle) vector=1` — the prefixed form, always, since
  the bare form makes ds9 reject the file — in the frame the serialized block declares. A
  provenance comment goes after the library's header line, and must never begin with `-`. See
  "The resulting export recipe" above for the exact target output.
- **Sky:** convert through `wcs.sub(['longitude','latitude'])` (finding 7), then through the
  AXIS 1/2/3 mapping to reach numpy axes, the same chain `plot_catalog._row_to_display` uses.

Anything skipped in either direction — unsupported shapes and `-` exclusions on import,
`z_range` and other non-ds9 attributes on export — is **collected and reported in a summary
dialog**. Silent loss is the exact failure mode finding 1 demonstrates, and the YAML file
stays the source of truth.

### Phase 3 — interactive layer

`pyql3/gui/viewers/region_layer.py`, one per `ImageViewer`, owning the graphics items and the
stored model:

- items parented to the ImageItem, so view rotation is inherited;
- removal via `ViewBox.removeItem()`, **never** `setParentItem(None)` — that leaves the item
  painted in the same scene (B7, documented at `plot_catalog._remove_scene_item`);
- re-place every item from stored raw coordinates whenever `apply_transforms`,
  `apply_axis_mapping` or `refresh_display` runs — the same staleness class as
  `update_tools_for_unit`;
- reuse plot_catalog's debounced-`TextItem` handling for labels during pan/zoom.

Also fixes a latent bug on the way: `BaseToolDialog.enable_draw_mode()` monkeypatches
`view.mouseDragEvent` and restores it from `self._old_drag`, so two tools in draw mode
already stomp on each other. Region drawing would be a third claimant. Add an exclusive-drag
arbiter on the viewer (`begin_exclusive_drag(owner)` / `end_exclusive_drag`) that both go
through.

### Phase 4 — the Region menu

New top-level menu: New Circle / Box / Arrow / Text (click-drag to place), Load Regions…,
Save Regions…, Export ds9 .reg…, Region List…, Delete All.

The Region List is a `BaseToolDialog` — so it must be added to `MainWindow.TOOL_DIALOG_ATTRS`
— with a table of type / position / size / label / colour / visible, inline edit, double-click
to zoom to a region, and delete. Right-click on a region: Edit properties…, Delete, Copy
coordinates (precedent in `plot_catalog.show_context_menu`).

Regions belong to the window's viewer, so each window keeps its own overlay, matching the
multi-window model.

### Phase 5 — edges

- `--regions file.yml|file.reg` on the command line, mirroring `--catalog`.
- Thousands of regions (someone converting a catalog) would crawl with one editable ROI
  each: above a threshold, render non-editable regions into a single static item, and
  `log`/status-bar what was capped rather than silently truncating.
- Region → tool handoff ("use this region as the Depth Plot aperture") is **out of scope**,
  but the model carries enough that it is a small follow-on.

### Phase 6 — tests, docs, packaging

- Qt-free: YAML round-trip; one ds9 read/write test per shape, arrows explicitly, as the
  shape the library cannot do; sky ↔ pixel against the OSIRIS-ordered fixture including a
  regression asserting the `wcs.celestial` NaN trap stays fixed; malformed `.reg` never
  raising; the skipped-shape report.
- Offscreen GUI: draw a region, flip and rotate through all four steps, assert it still
  covers the same raw pixels; save in one window, load in another.
- `uv add regions` with the lock committed (CI runs `uv sync --frozen`, so an unlocked
  dependency fails the release build), `collect_all('regions')` in `QuickLook3.spec`, and
  `_geometry` added to the bundled-asset greps in `build_app.sh` and
  `.github/workflows/release.yml`.
- `docs/tools.md` for the region tools, `docs/cli.md` for the flag, and AGENTS.md for the two
  new invariants (single coordinate mapper; region geometry stored in raw FITS pixels, never
  display coordinates) plus the note that arrows bypass `regions`.

## Risks

- **Off-by-half / off-by-one** between FITS, numpy and pyqtgraph conventions. Finding 3
  removes the ds9 side of it; the rest is pinned by Phase 0's tests.
- **Angle signs under flips and 90° rotations.** Easy to get subtly wrong, and only visible
  as a box drawn at the mirrored PA. All 8 combinations are tested.
- **ds9 interop is asymmetric.** `z_range` and per-region extras cannot survive a `.reg`
  write; the summary dialog is what keeps that from being data loss.
- **Adding `regions` grows the frozen bundle** by 7 compiled extensions plus its astropy
  usage. Check the `.dmg`/`.exe` size delta before release.
