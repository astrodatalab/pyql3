# Record: ds9-style regions (circle, box, arrow, text)

Status: **complete** — all six phases landed between 2026-08-05 and 2026-08-06, starting from
commit `e2be099`. This file is kept as the record of how it was built and, more usefully, of what
was *measured*: region memory and load times, the pan-frame cost of labels, and the ds9 format
ladder run in ds9 itself.

**It is not where the rules live.** Anything a future change has to obey — the coordinate space
regions are stored in, the two render modes, the Qt traps, and the verified ds9 `.reg` constraints
— is in the "Regions" section of `AGENTS.md`. The user-facing description is `docs/tools.md` §9.
If this file and either of those disagree, they win.

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

Reproduce with `uv run python -c ...`; `regions` is a project dependency as of Phase 2.

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

### Phase 0 — one canonical coordinate mapper ✅ DONE

Landed as `pyql3/core/coords.py` + `ImageViewer.orig_to_display()` / `.display_to_orig()` /
`.orig_spatial_dims()`, with all three call sites rewired and 181 cases in
`tests/test_coords.py`. This closed `BUGS.md` B13 and B14, and writing the angle mapping turned
up **B20** (the PA compass mirrored the rotation instead of the array), which is fixed too —
`ImageViewer.north_east_display_angles()`, covered by `tests/test_position_angle.py`. So the
angle mapping regions will need is in place and tested before anything depends on it.

Naming note for the phases below: **"orig" means `transposed_data` indices**, the coordinate
along whichever FITS axis is currently mapped to X or Y. Earlier drafts of this plan said
"raw FITS pixels", which is wrong in the project's vocabulary — `raw_data` is the untouched
FITS array in original axis order, and choosing which axis is X is a separate step. Region
geometry is stored in **orig** coordinates.

The original scope, for the record:



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

### Phase 1 — model + native YAML ✅ DONE

Landed as `pyql3/core/regions_model.py` (`Circle`, `Box`, `Arrow`, `Text`, `SkyAnchor`,
`RegionList`) with 67 cases in `tests/test_regions_model.py`. `pyyaml` moved into
`[project] dependencies` with the lock committed. Notes for the phases that build on it:

- **The `SkyAnchor` container exists but nothing populates it yet.** Filling it needs a WCS,
  and the `wcs.sub(['longitude','latitude'])` trap is documented in Phase 2, so that is where
  the conversion belongs. Phase 1 defines and round-trips the record only; pixel geometry is
  authoritative while `frame == "image"`.
- **Shapes are keyword-only** (`Circle(x=…, y=…, radius=…)`). A dataclass cannot otherwise put
  a subclass's mandatory geometry after the base class's defaulted styling, and positional
  construction would silently reorder geometry as fields are added.
- **Arrows are stored as ds9 stores them** — tail, length, heading — with `Arrow.end` and
  `Arrow.from_points()` for the two-handled ROI that Phase 3 will use.
- **Unknown fields are rejected, not ignored**, listing what is allowed. A silently dropped
  `colour:` in a hand-edited file looks like the setting simply does not work. Every problem in
  a file is reported at once for the same reason.
- **A ds9 `.reg` file handed to the YAML loader is recognised** and answered with a pointer to
  the ds9 reader, rather than "found str" — it is by far the likeliest wrong file. Tests also
  pin the reverse: a valid file whose *labels* contain ds9 syntax must still load.
- `pyyaml` is pure Python and imported normally, so `QuickLook3.spec` should need no entry —
  confirm at the next build rather than assuming.

The original scope, for the record:



`pyql3/core/regions_model.py`: dataclasses `Circle`, `Box` (with PA), `Arrow`, `Text`, with a
shared attribute block — label, colour, line width, dash, font size, tag/group, visibility —
plus two things ds9 has no room for:

- `z_range: [zmin, zmax] | None` — a region that only applies over an emission line's
  channels,
- `frame: image | sky` — whether the stored geometry is pixels or `(ra, dec)` plus angular
  sizes, so a region file survives reloading a dithered frame with a different WCS.

Geometry is stored in **0-based orig coordinates** (`transposed_data` indices, see Phase 0),
never display coordinates, with the sky position written alongside when a WCS exists. Native file is
versioned YAML (`format: pyql3-regions/1`), loaded with **`yaml.safe_load` only**.

`pyyaml` is currently only a transitive *dev* dependency (via mkdocs) — it has to move into
`[project] dependencies`. Note also that `pydantic` is a declared dependency that nothing
imports; validate by hand here rather than adopting it, and treat "use pydantic or drop it"
as a separate question.

### Phase 2 — ds9 `.reg` IO ✅ DONE

Landed as `pyql3/core/ds9_regions.py` (`to_ds9` / `from_ds9` / `Report`) and
`pyql3/core/sky.py` (`CelestialMap`), with 43 cases in `tests/test_ds9_regions.py`. `regions`
added with the lock committed, `collect_all('regions')` in the spec, and `regions/_geometry`
added to the bundle greps in `build_app.sh` and `release.yml`. Also added
`ImageViewer.display_axis_indices()`, consolidating a fourth copy of the AXIS-combo expression
(one of which defaulted to `AXIS 1` — the *wavelength* axis on an OSIRIS cube).

Found while building it, beyond what the plan anticipated:

- **`regions` reads `textangle` but never writes it.** It arrives as `visual['rotation']` and
  is absent from `serialize()` output, so a rotated label loses its angle. Rotated text is
  therefore kept away from the library entirely and hand-written as `# text(...) textangle=…`;
  un-rotated text still goes through it. This is a second gap alongside `vector`, and it is
  measured, not inferred.
- **ds9's `image` frame means FITS axes 1 and 2, always.** An OSIRIS cube is displayed on axes
  3 and 2, so image-frame coordinates are simply a different plane between the two
  applications. `to_ds9(frame="auto")` therefore writes *sky* coordinates whenever the display
  is not on axes 1 and 2 and a WCS allows it, and says so in the `Report`; on import, image
  frame regions on such a cube are loaded but flagged. This is the single biggest interop trap
  and the plan did not have it.
- Box angles convert correctly through sky frames (20° pixel → 160° in the file → 20° back),
  so the library owns every angle conversion for shapes. Only arrows are ours.

**Settled by ds9 (files 22-25, checked 2026-08-06). No open format questions remain.**

| File | Result |
|------|--------|
| `22_mixed_frames.reg` | ✅ a coordinate-system change part way through a file is accepted |
| `23_sky_vector.reg` + `24_image_vector_reference.reg` | ✅ a sky-frame `# vector(...)` is drawn, and on an unrotated field its direction matches an image-frame vector at the same angle |
| `25_sky_angle_on_rotated_field.reg` (on `check_rotated.fits`) | ✅ on a 30°-rotated field a sky-frame vector lies along a sky-frame box at the same angle, pointing up and right as predicted (~15° CCW from screen right) |

File 25 was the one that could actually decide it. Files 23 and 24 agreed on an *unrotated*
field, where the sky and image angle conventions coincide — verified locally: the conversion is
the identity at zero rotation — so they would have agreed whichever convention ds9 used. On a
30°-rotated field the two differ by 30°, and Keck data is rotated as a matter of course. Comparing
against a *box* is what made it decidable, because `regions` already implements the box
convention.

So **ds9 measures a vector's angle in a sky frame from the sky axes, exactly as it does a box's**,
and a sky export writes its arrows on the sky too — tail as RA/Dec, length in degrees, direction
converted by `_pixel_angle_to_sky`. Leaving them in pixel coordinates would have meant a "sky"
file whose arrows do not follow the field, which defeats the only reason to export sky
coordinates. The conversion is borrowed from the library's own box handling rather than
reimplemented, so there is one implementation of ds9's convention and it is the tested one.
`tests/test_ds9_regions.py::test_a_sky_angle_is_not_the_image_angle_on_a_rotated_field` pins it
(45° pixel → 75° sky at 30° rotation).

The original scope, for the record:



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

### Phase 3 — interactive layer ✅ DONE

Landed as `pyql3/gui/viewers/region_layer.py` (`RegionLayer`), created by every `ImageViewer` as
`self.region_layer`, with 63 cases in `tests/test_region_layer.py`. Also added
`ImageViewer.display_changed` (emitted whenever the plane's geometry changes) and the
exclusive-drag arbiter. Notes for Phase 4:

- **The layer is the API the menu should drive**: `add` / `remove` / `clear` / `set_regions` /
  `restyle` / `begin_draw(kind, **attributes)` / `cancel_draw` / `place_at`, with signals
  `regions_changed`, `region_drawn` and `draw_mode_changed`. It puts nothing on screen itself —
  the label text and colour of a new region come in as `begin_draw` attributes, so the dialogs
  stay in Phase 4.
- **Model regions are compared by value**, being dataclasses, so two identical circles are `==`.
  Every lookup in the layer is by identity (`is`); a region list dialog must do the same.
- **`begin_draw` also fixed a bug older than regions.** `BaseToolDialog.enable_draw_mode` saved
  and restored `ViewBox.mouseDragEvent` itself, so two tools in draw mode corrupted it between
  them and left the view unable to pan. Ownership now goes through
  `ImageViewer.begin_exclusive_drag`, which revokes the previous owner and un-checks its button.
- Text regions are two items: a `TextItem` for the label and a small `TargetItem` as the drag
  handle. `pg.TargetItem` supports its own label, but it cannot be rotated, and `textangle`
  round-trips through ds9.
- One pyqtgraph trap worth keeping: `ROI.setAngle(a, centerLocal=[0.5, 0.5])` rotates about a
  point *half a pixel* from the corner, not the centre — `centerLocal` is in local pixels, while
  `center` is normalised. The layer uses `center=[0.5, 0.5]`, which is verified to hold a box's
  centre still.

The original scope, for the record:



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

### Phase 4 — the Region menu ✅ DONE

Landed as a top-level **Region** menu on `MainWindow`, `pyql3/gui/tools/region_list.py`
(`RegionListDialog`, in `TOOL_DIALOG_ATTRS`) and `pyql3/core/regions_io.py` (the format dispatch),
with 39 cases in `tests/test_region_ui.py`.

- **Loading picks the format by content, not by suffix** — a ds9 file named `.yml` is an ordinary
  thing to be handed. Saving picks it by suffix, so **Export ds9 Regions...** forces `.reg` rather
  than writing YAML into a file the user named `.reg`.
- **The Phase 1 sky-anchor gap is closed.** `regions_io.with_sky_anchors()` fills in RA/Dec, the
  angular sizes and the sky angle when a WCS allows it, into *copies* — the live regions are left
  alone, so saving has no side effects on what is on screen.
- **The list dialog is a view of the layer, not a second copy.** Editing a cell writes to the model
  region and re-places it; dragging on the image writes back and the table refreshes. Both
  directions go through `regions_changed`, so they cannot drift.
- **This fixed `BUGS.md` M11 on the way.** The test helper reached a menu entry by walking
  `menuBar().actions()` and calling `.menu()` — which makes the Python wrapper a transient owner,
  so PySide6 destroyed the menu and every action in it before the helper returned. Every menu is
  now stored on `self`; reach one through its attribute, never through `action.menu()`.
- One trap for later: `start_drawing_region` shows a modal `QMessageBox` when nothing is loaded, so
  any test firing that action on an empty window must stub it or the suite hangs — the same hazard
  AGENTS.md already records for `install_cli_tool`.

**Follow-up from testing (2026-08-06), now done.** Two problems came out of real use:

- **Right-clicking a region raised in the terminal** — `BUGS.md` M12. pyqtgraph's
  `ROI.raiseContextMenu` walks up to the ImageItem we parent to, whose `getContextMenus()` returns
  `[None]`, which pyqtgraph then refuses to add to a menu. Regions are the first ROIs here with
  `removable=True`, the only way to reach that code. `RegionItemInteraction` now handles right-click
  and double-click itself, and a test asserts the inherited path *still* raises so the override
  cannot be dropped.
- **A region could be drawn but not changed.** `pyql3/gui/dialogs/region_properties.py` is the
  ds9-style editor: double-click a region (or Properties… from its context menu, or the Region
  List) and every field the model carries is editable — colour, line width, text, text size, angle,
  dash, tag, visibility, geometry and the channel range. Apply writes through and redraws; Cancel
  restores, including anything a previous Apply wrote. Dialogs are keyed by `id(region)` so the same
  region reuses its editor, which matters because two identical regions are `==` as dataclasses.
- While fixing that: **a shape's label is now drawn**, as ds9 draws `text={...}` beside a circle or
  box. Without it the text size and colour fields meant nothing for anything but a text region.

`QMenu.exec` is modal, so `build_region_menu` is separate from `show_region_menu`; a test that pops
a menu up hangs the suite.

**Also from testing: an arrow's head pointed the wrong way** (`BUGS.md` M13). The head is
`pxMode=True`, which means `ItemIgnoresTransformations` and therefore screen coordinates with y
*downward*, while the line's angle has y upward — so it needs `180 - direction`, not
`direction + 180`. The wrong form was copied from the PA compass, where it is correct because those
arrows are `pxMode=False` and are transformed with the view. Copying an angle convention between a
transformed and an untransformed item is the trap; the two expressions agree only at 0° and 180°,
which is why horizontal arrows looked fine.

The original scope, for the record:



New top-level menu: New Circle / Box / Arrow / Text (click-drag to place), Load Regions…,
Save Regions…, Export ds9 .reg…, Region List…, Delete All.

The Region List is a `BaseToolDialog` — so it must be added to `MainWindow.TOOL_DIALOG_ATTRS`
— with a table of type / position / size / label / colour / visible, inline edit, double-click
to zoom to a region, and delete. Right-click on a region: Edit properties…, Delete, Copy
coordinates (precedent in `plot_catalog.show_context_menu`).

Regions belong to the window's viewer, so each window keeps its own overlay, matching the
multi-window model.

### Phase 5 — edges ✅ DONE (measured, 2026-08-06)

The static-render cap is no longer a guess. Measured on a 64×64×20 cube, one circle per region,
`QT_QPA_PLATFORM=offscreen` (so these are construction and update costs, not painting):

| regions | RSS over baseline | per region | time to add | `refresh()` | Region List build |
|---------|-------------------|-----------:|------------:|------------:|------------------:|
| 1,000 | 40 MB | 41 kB | 0.23 s | 0.009 s | 0.10 s |
| 5,000 | 240 MB | 49 kB | 4.4 s | 0.060 s | 2.2 s |
| 10,000 | 600 MB | 62 kB | 27 s | 0.117 s | 16 s |

A region *as data* is **0.21 kB** — the graphics items cost 200–300× the model. Profiling the add
path in the expensive regime: 55% is `SignalInstance.connect` (pyqtgraph's ROI and its handles wire
up ~7 signals per region) and most of the rest is `setParentItem` → pyqtgraph's `_updateView` /
`itemChange` bookkeeping. Both are inherent to one QGraphicsItem per region, not to anything this
code does. So **tens of thousands of interactive regions is not reachable by tuning**; 10,000 is
already 0.6 GB and half a minute to load, and 100,000 would be ~6 GB.

The same 200,000 regions drawn as a **single `ScatterPlotItem`** cost 0.42 kB each and 0.098 s to
build — 130× less memory, 300× faster:

| regions | RSS over baseline | per region | build | re-place |
|---------|-------------------|-----------:|------:|---------:|
| 10,000 | 4 MB | 0.41 kB | 0.005 s | 0.004 s |
| 200,000 | 83 MB | 0.42 kB | 0.098 s | 0.084 s |

**Built, and measured on a real cube.** Above `region_layer.INTERACTIVE_LIMIT` (500) the layer stops
building per-region items and draws the whole set as one item per distinct style: a
`ScatterPlotItem` for circles, a `+` marker for text positions, and one NaN-broken `PlotDataItem`
polyline for boxes and arrows (barbs included, so an arrow still reads as one). `plot_catalog`
already renders catalogue markers this way, so the pattern was established here.

20,000 regions, end to end through the GUI:

| | before the cap (extrapolated) | with the cap |
|---|---|---|
| load from file | ~2 min, ~1.2 GB | **2.1 s, 154 MB** |
| scene items | 40,000 | **1** |
| rotate 90° | ~0.25 s | **0.039 s** |
| step one channel | ~0.01 s | **0.038 s** |
| open the Region List | ~60 s | **0.001 s** (declines to list, says so) |
| delete 19,800 | minutes | **0.12 s**, and the remaining 200 are draggable again |

The switch is announced in the status bar, and crossing back below the limit restores per-region
interaction. The Region List refuses to fill in above `LIST_LIMIT` (2,000) rather than showing the
first 2,000 as if the rest had been lost — `QTableWidget` needs 16 s to build 10,000 rows, on every
drag frame. A `QAbstractTableModel` behind a `QTableView` would lift that ceiling if it ever needs
lifting.

**Labels are hidden while the view moves**, the optimisation `plot_catalog` already uses for
catalogue labels — the same trick, found to apply here for the same reason. Measured on 400
labelled regions by forcing real paints: **34.2 ms per pan frame with the labels drawn, 23.4 ms
without** (32%). Text is by far the most expensive thing on the overlay to paint, and a drag
repaints every label on every frame. `sigRangeChanged` hides them at once and a 200 ms debounce
brings back only those inside the visible rect, which also caps the cost when zoomed in. The shapes
themselves stay visible — losing the regions mid-drag would be worse than the frame rate.

Two things the measurement also showed, not acted on: the ROI *handles* are another ~15% of a frame
(18.1 ms against 21.2 ms for 400 unlabelled regions), and they could be hidden during a pan on the
same timer since they cannot be grabbed mid-drag; and a label near the edge of the view still paints
into it, which is why the cull grows the rect by 10% before testing.

**`--regions` is in**, mirroring `--catalog`, detecting the format from the file's contents and
reporting to stderr rather than a modal dialog — a flag was typed, so there is a terminal to read,
and a dialog would block an automated launch. `BUGS.md` M6 is fixed at the same time: `--catalog`
was the one command-line path that skipped `expanduser`, so `--catalog ~/cat.csv` was silently
ignored. A test now asserts every flag expands `~`.

**Three defects found while building this, all fixed:**

- `add()` ended by refreshing *every* region's channel visibility, so a bulk load was O(N²) — 48 s
  for 10,000 regions, more than half the total. Now per-entry
  (`tests/test_region_layer.py::test_adding_a_region_does_not_touch_the_others`).
- `item_for` / `label_for` / `remove` / `restyle` scanned the entry list, so deleting a selection of
  N was O(N²). Now an `id(region)` → entry dict, with tests that it is emptied on removal so it
  cannot hold graphics items alive.

- **A reproducible segfault.** `restyle()` destroyed a region's items and immediately rebuilt them,
  which handed the old C++ objects to the garbage collector — and it then ran *inside* the
  construction of the replacements, crashing in `pg.ROI.addScaleHandle`. It reproduced in two runs
  out of three, only in a full-suite run. `restyle()` now updates the items in place and only
  rebuilds when a label appears or disappears, and `_destroy_items` holds removed items until the
  next event-loop turn so the collector never chooses the moment. Five consecutive clean runs.

The per-drag-frame table rebuild is no longer a scaling problem: above `LIST_LIMIT` the table holds
nothing, and below it there are at most 2,000 rows. It would still be worth moving to a
model/view if the limit is ever raised.

The original scope, for the record:



- `--regions file.yml|file.reg` on the command line, mirroring `--catalog`.
- Thousands of regions (someone converting a catalog) would crawl with one editable ROI
  each: above a threshold, render non-editable regions into a single static item, and
  `log`/status-bar what was capped rather than silently truncating.
- Region → tool handoff ("use this region as the Depth Plot aperture") is **out of scope**,
  but the model carries enough that it is a small follow-on.

### Region toolbar (added after Phase 5, 2026-08-06)

`pyql3/gui/region_toolbar.py`: a small vertical bar on the left with the four shapes, the region
list and a clear-all. **Off by default** and toggled from **Region ➔ Region Toolbar**, with the
choice remembered in `~/.pyql3/config.json` — the image is what the window is for, and a permanent
bar takes width from it.

- **The icons are painted with `QPainter`, not shipped as files.** Four outlines and a letter are
  less work to draw than to draw, export, bundle and verify, they take their colour from the running
  palette so they suit a light or a dark theme, and they stay crisp at any size. `QuickLook3.spec`
  and the build's asset checks are untouched, which is the real saving.
- **The buttons call the window**, the same methods the menu does, so the two cannot drift and the
  clear button inherits the menu's confirmation rather than skipping it.
- The shape buttons are exclusive and follow `RegionLayer.draw_kind`, so the pressed button says
  what a drag will draw — and releases itself when a tool dialog revokes the drag or the window
  refuses (nothing loaded, or a cancelled label prompt).
- Offered on the left or right edge only: along the top it would push the image down rather than
  narrowing it.

**A bug found by rendering the window and looking at it:** a shape's caption was drawn rotated by
the shape's own angle, so an arrow's label lay on its side at the arrow's 55° and a rotated box's
caption tipped over with it. A box's angle rotates the *box* and an arrow's is its *heading*;
neither is a text angle, and ds9 treats `textangle` as a property of a text region alone. Only a
`Text` region now turns its label. The same conflation of two angle meanings as `BUGS.md` M13 — one
worth watching for wherever an angle is passed along.

### Text placement reworked (2026-08-06)

Placing a label used to ask for the text first and then want a *drag*, which had it backwards twice
over. Now: arm the tool, **click where the label goes**, and the prompt follows — naming a thing
after pointing at it, and telling the prompt where it landed ("Label for the region at (9.0, 6.0)").

- **A click, not a drag.** A click without movement never reaches `mouseDragEvent`, so text needed a
  small drag to appear at all, and the drag drew a rubber-band line — suggesting an orientation a
  label cannot have, since it is drawn horizontally. The layer now listens for
  `sigMouseClicked` while the text tool is armed, and swallows drags without drawing anything.
- **The layer still shows no dialogs.** `begin_draw("text", ask_text=…)` takes a callback, called
  with the position in orig coordinates once the click lands; returning nothing places nothing.
  Same shape as `PollingDialog`'s `confirm_takeover`.
- **A pre-existing double-emit fixed on the way:** `place_at` emitted `region_drawn` *and* so did
  the drag handler that calls it for a click-sized drag, so such a region was announced twice. One
  private `_drawn()` now owns adding-and-announcing, with a test that counts the emissions.

### A text region is now its own handle (2026-08-06)

A text region used to carry a small crosshair to grab it by — and since the marker and the label
shared an anchor, the marker sat on top of the very text it was there to move. The label is already
a visible thing of exactly the right size, so it makes a better handle than anything added beside
it: click, drag or right-click **the text itself**.

`RegionTextItem` subclasses `pg.TextItem`, which is a `GraphicsObject` and so can take mouse events
once it says which buttons it wants. The drag follows `pg.TargetItem`'s pattern and the signal is
named `sigPositionChanged` to match, so the layer's existing wiring picked it up unchanged. Hovering
outlines the text, and the cursor becomes a move cursor — without a marker, something has to say the
text can be dragged.

Two details worth keeping:

- **A text region must have text.** That was already enforced by the model and the properties
  dialog; it is now load-bearing, because an empty label would be a region with nothing to click.
- **A shape's caption is explicitly mouse-transparent.** A `QGraphicsItem` accepts every mouse
  button by default, so a circle's caption would otherwise sit in front of the circle and swallow
  presses meant for the shape or for panning. Only a text region's label is interactive.

### Region labels in a large set (2026-08-06)

Reported: a ds9 file of 2,000 named stars drew every circle and **none of the text**. That was a
deliberate choice in the aggregate overlay — one `TextItem` per region is the cost the overlay
exists to avoid — and it was the wrong one. For a catalogue of named stars the names *are* the
data.

Fixed by doing what `plot_catalog.update_visible_text_labels` already does, which was the right
precedent to follow:

- **Cull to the viewport.** Only labels inside the visible rect are built, so zooming in costs
  proportionally less: 2,000 labels take 0.45 s to build with the whole field in view, 323 take
  0.13 s zoomed in.
- **Hide them while panning**, rebuild 200 ms after the view settles — already in place from the
  earlier pan work, now applied to the overlay's labels too.
- **Let the user decide.** **Region ➔ Show Region Labels**, remembered, defaulting to on — the same
  switch the catalogue tool offers as *Show Names*. An earlier attempt at this refused to draw more
  than 300 labels on the grounds that they would be unreadable; that is a judgement for the person
  looking at the field, not for the layer.

What remains is a `LABEL_SAFETY_LIMIT` of 5,000, which is a hang guard rather than a readability
rule: at ~0.18 ms per label, building an unbounded set would lock the window up. When it bites, the
status bar says so and points at the toggle.

### Spawning a region from the right-click menu (2026-08-06)

The viewer's own context menu gained a **New Region** submenu — Circle, Box, Arrow, Text... — which
puts a **default-sized** region where the click landed. Pointing at a feature and getting a region
there is quicker than dragging one out, and the size is easy to change afterwards from a handle, the
properties dialog or the Region List. Text still asks for its label, at that position.

`RegionLayer.place(kind, x, y, ask_text=…)` is the API; `place_at` (the click-to-place path) now
delegates to it, so a region created by either route is identical. Default sizes are named constants
rather than literals buried in a branch.

Right-clicking *on* a region still gives that region's own menu (Properties, Copy Coordinates,
Delete) — the new entries appear when clicking the image itself.

**A latent bug fixed on the way** (`BUGS.md` M15): the stored right-click position was clipped using
`transposed_data`'s extents with x and y swapped, and against the *stored* plane rather than the
displayed one. It already misplaced **Depth Plot** and **Gaussian Fit** near the edge of a
non-square cube; spawning regions would have inherited it.

### Phase 6 — tests, docs, packaging ✅ DONE (2026-08-06)

Landed: `docs/tools.md` §9 (the menu, the toolbar, drawing and right-click spawning, properties,
the Region List, labels in a crowded field, the file formats, and what changes above 500 regions),
`docs/cli.md` for `--regions`, `docs/developer.md` for the module map and the two render modes, and
an `AGENTS.md` "Regions" section holding the invariants, the four Qt traps, and the ds9 interop
rules verified below — so retiring this file loses nothing that matters. The cross-window test the
plan asked for is `test_regions_saved_in_one_window_load_in_another`; the rest of the tests and the
packaging checks landed with their own phases.

The original scope:

- Qt-free: YAML round-trip; one ds9 read/write test per shape, arrows explicitly, as the
  shape the library cannot do; sky ↔ pixel against the OSIRIS-ordered fixture including a
  regression asserting the `wcs.celestial` NaN trap stays fixed; malformed `.reg` never
  raising; the skipped-shape report.
- Offscreen GUI: draw a region, flip and rotate through all four steps, assert it still
  covers the same orig pixels; save in one window, load in another.
- `uv add regions` with the lock committed (CI runs `uv sync --frozen`, so an unlocked
  dependency fails the release build), `collect_all('regions')` in `QuickLook3.spec`, and
  `_geometry` added to the bundled-asset greps in `build_app.sh` and
  `.github/workflows/release.yml`.
- `docs/tools.md` for the region tools, `docs/cli.md` for the flag, and AGENTS.md for the two
  new invariants (single coordinate mapper — already added with Phase 0; region geometry
  stored in orig coordinates, never display coordinates) plus the note that arrows bypass `regions`.

## Risks

- **Off-by-half / off-by-one** between FITS, numpy and pyqtgraph conventions. Finding 3
  removes the ds9 side of it; the rest is pinned by Phase 0's tests.
- **Angle signs under flips and 90° rotations.** Easy to get subtly wrong, and only visible
  as a box drawn at the mirrored PA. All 8 combinations are tested.
- **ds9 interop is asymmetric.** `z_range` and per-region extras cannot survive a `.reg`
  write; the summary dialog is what keeps that from being data loss.
- **Adding `regions` grows the frozen bundle** by 7 compiled extensions plus its astropy
  usage. Check the `.dmg`/`.exe` size delta before release.
