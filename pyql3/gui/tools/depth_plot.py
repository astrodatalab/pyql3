import os
import re
import pathlib
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QGridLayout, QLabel, QComboBox, QCheckBox, QSpinBox, QHBoxLayout, QGroupBox, QPushButton, QDoubleSpinBox, QFileDialog, QWidget
from PySide6.QtCore import Qt, QDir
from pyql3.core.spectral_photometry import (
    ApertureError,
    annulus_spectrum,
    subtract_background,
)
from pyql3.gui.tools.base_tool import BaseToolDialog, as_center

#: Opening aperture radius, in pixels.
DEFAULT_APERTURE_RADIUS = 3.0

#: The sky annulus is defined *relative to the aperture*, not as two free numbers: it starts
#: one pixel outside the aperture and is two pixels wide. Keeping it a relationship rather
#: than a pair of constants is what makes it survive a change of aperture -- an annulus left
#: at fixed radii while the aperture grew would end up measuring sky from inside the source,
#: which subtracts signal and looks like nothing went wrong.
SKY_INNER_GAP = 1.0
SKY_WIDTH = 2.0
DEFAULT_INNER_RADIUS = DEFAULT_APERTURE_RADIUS + SKY_INNER_GAP
DEFAULT_OUTER_RADIUS = DEFAULT_INNER_RADIUS + SKY_WIDTH

#: Where a spectral line's name sits, as a fraction of the view height measured *up from the
#: bottom*, with the name standing up from its row. Four rows, so names too close together to
#: read side by side step up instead of colliding.
#:
#: The floor, not the ceiling: a spectrum has its free space below the trace. Moved to a
#: gutter at the top these were harder to read *and* still crossed the spectrum, because
#: whenever autoranging has to fit a subtracted curve near zero the continuum is pushed hard
#: against the top of the view. The rows are stepped only slightly apart -- they are there to
#: separate horizontally crowded names, not to spread labels over half the plot.
GUTTER_LEVELS = (0.08, 0.16, 0.24, 0.32)

#: Colour of the two sky rings. The same orange as the Background curve, so the annulus on
#: the image and the line on the plot are visibly one thing.
_RING_COLOR = (255, 140, 0)


def latex_to_html(text):
    if not text:
        return ""
    
    s = text.strip()
    # Strip wrapping $$...$$ or $...$
    if s.startswith("$$") and s.endswith("$$") and len(s) >= 4:
        s = s[2:-2].strip()
    elif s.startswith("$") and s.endswith("$") and len(s) >= 2:
        s = s[1:-1].strip()
        
    def replace_math(match):
        return match.group(1) or match.group(2) or ""

    s = re.sub(r"\$\$(.*?)\$\$|\$(.*?)\$", replace_math, s)

    # Greek letters
    greek = [
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
        "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau",
        "upsilon", "phi", "chi", "psi", "omega",
        "Alpha", "Beta", "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi",
        "Sigma", "Phi", "Psi", "Omega"
    ]
    for g in greek:
        pattern = "\\\\" + g + "(?![a-zA-Z])"
        s = re.sub(pattern, "&" + g + ";", s)

    # Spacing & symbols
    s = s.replace("\\;", "&nbsp;").replace("\\ ", "&nbsp;").replace("\\quad", "&nbsp;&nbsp;")
    s = s.replace("\\AA", "&#8491;").replace("\\angstrom", "&#8491;").replace("\\pm", "&plusmn;")

    # Subscripts: _{abc} or _abc
    s = re.sub(r"_\{([^}]+)\}", r"<sub>\1</sub>", s)
    s = re.sub(r"_([a-zA-Z0-9&;#]+)", r"<sub>\1</sub>", s)

    # Superscripts: ^{abc} or ^abc
    s = re.sub(r"\^\{([^}]+)\}", r"<sup>\1</sup>", s)
    s = re.sub(r"\^([a-zA-Z0-9&;#]+)", r"<sup>\1</sup>", s)

    return s


class PixelIndexAxis(pg.AxisItem):
    """Top axis displaying 0-indexed channel slice numbers when the bottom X-axis displays physical wavelengths."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.wavelengths = None

    def tickStrings(self, values, scale, spacing):
        if self.wavelengths is None or len(self.wavelengths) == 0:
            return super().tickStrings(values, scale, spacing)

        try:
            indices = np.interp(values, self.wavelengths, np.arange(len(self.wavelengths)))
            return [f"{int(round(idx))}" for idx in indices]
        except Exception:
            return super().tickStrings(values, scale, spacing)


class DepthPlotDialog(BaseToolDialog):
    def __init__(self, parent=None, image_viewer=None, initial_center=None):
        super().__init__(parent, image_viewer, "Plot Window")
        # A Qt signal may hand us its `checked` flag instead of a centre
        initial_center = as_center(initial_center)
        self.resize(700, 800)
        
        # Top Controls
        top_layout = QHBoxLayout()
        self.setup_draw_button(top_layout)

        self.btn_export = QPushButton("Export...")
        self.btn_export.setToolTip("Export plot data (CSV, Image, SVG, Vector)")
        self.btn_export.clicked.connect(self.open_export_dialog)
        top_layout.addWidget(self.btn_export)

        top_layout.addWidget(QLabel("Type:"))
        
        self.combo_type = QComboBox()
        self.combo_type.addItems(["Depth Plot", "Horizontal Cut", "Vertical Cut"])
        self.combo_type.currentIndexChanged.connect(self.update_plot)
        top_layout.addWidget(self.combo_type)
        
        # Shape, Radius and the combine method used to live here. They describe *what is
        # measured*, not what the window does, and they belong beside the rest of the
        # aperture in the EXTRACTION group below -- where the aperture is described once
        # rather than twice, in two parameterisations, at opposite ends of the dialog.
        top_layout.addStretch()
        self.layout.addLayout(top_layout)
        
        # Plot Widget
        self.top_axis = PixelIndexAxis(orientation='top')
        self.plot_widget = pg.PlotWidget(background='w', axisItems={'top': self.top_axis})
        
        self.plot_widget.setLabel('bottom', "Wavelength", units="µm")
        unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
        self.plot_widget.setLabel('left', f"Intensity ({unit})")
        
        # Dual axis setup
        self.plot_widget.showAxis('top')
        self.plot_widget.getAxis('top').setPen('k')
        self.plot_widget.getAxis('top').setTextPen('k')
        self.plot_widget.getAxis('top').setLabel("Slice Index (pixels)")
        
        self.plot_widget.getAxis('bottom').setPen('k')
        self.plot_widget.getAxis('bottom').setTextPen('k')
        self.plot_widget.getAxis('left').setPen('k')
        self.plot_widget.getAxis('left').setTextPen('k')
        
        self.plot_widget.getAxis('right').setPen('k')
        self.plot_widget.showAxis('right')
        
        self.layout.addWidget(self.plot_widget, stretch=1)
        
        # Top *right*: a spectrum is read left to right, and at (10, 10) the box covered the
        # first few percent of every curve -- the Source trace ran behind it in every state.
        # Opaque, or the entries are drawn over whatever does pass underneath.
        self.plot_legend = self.plot_widget.addLegend(offset=(-10, 10),
                                                      brush=pg.mkBrush(255, 255, 255, 220),
                                                      pen=pg.mkPen(180, 180, 180),
                                                      labelTextColor='k')
        # Built without `name=`, which would enrol them in the legend before they have data.
        # `sync_legend()` owns membership: see there for why an empty curve is not listed.
        self.plot_data = self.plot_widget.plot([], [], pen=pg.mkPen('k', width=2.5))
        # Solid, like Source: a dashed line breaks up narrow spectral features, and a
        # reader cannot tell a gap in the dash from a gap in the spectrum.
        self.plot_bg = self.plot_widget.plot([], [], pen=pg.mkPen((255, 140, 0), width=2.5))
        self.plot_sub = self.plot_widget.plot([], [], pen=pg.mkPen('r', width=2.5))
        self.plot_legend.addItem(self.plot_data, "Source")
        
        # Crosshair / Hover Label
        self.lbl_cursor = QLabel("X: --  Y: --")
        self.layout.addWidget(self.lbl_cursor)
        
        # Proxy for mouse move
        self.proxy = pg.SignalProxy(self.plot_widget.scene().sigMouseMoved, rateLimit=60, slot=self.mouse_moved)
        
        # Plot Axes GroupBox
        group_axes = QGroupBox("PLOT AXES")
        axes_layout = QGridLayout(group_axes)
        
        self.spin_x_min = QDoubleSpinBox(); self.spin_x_min.setRange(-1e9, 1e9); self.spin_x_min.setDecimals(4)
        self.spin_x_max = QDoubleSpinBox(); self.spin_x_max.setRange(-1e9, 1e9); self.spin_x_max.setDecimals(4)
        btn_set_x = QPushButton("SET")
        btn_auto_x = QPushButton("Auto")
        self.chk_fix_x = QCheckBox("Fix")
        self.chk_log_x = QCheckBox("Log")
        
        axes_layout.addWidget(QLabel("X Range:"), 0, 0)
        axes_layout.addWidget(self.spin_x_min, 0, 1)
        axes_layout.addWidget(QLabel("to"), 0, 2)
        axes_layout.addWidget(self.spin_x_max, 0, 3)
        axes_layout.addWidget(btn_set_x, 0, 4)
        axes_layout.addWidget(btn_auto_x, 0, 5)
        axes_layout.addWidget(self.chk_fix_x, 0, 6)
        axes_layout.addWidget(self.chk_log_x, 0, 7)
        
        self.spin_y_min = QDoubleSpinBox(); self.spin_y_min.setRange(-1e9, 1e9); self.spin_y_min.setDecimals(4)
        self.spin_y_max = QDoubleSpinBox(); self.spin_y_max.setRange(-1e9, 1e9); self.spin_y_max.setDecimals(4)
        btn_set_y = QPushButton("SET")
        btn_auto_y = QPushButton("Auto")
        self.chk_fix_y = QCheckBox("Fix")
        self.chk_log_y = QCheckBox("Log")
        
        axes_layout.addWidget(QLabel("Y Range:"), 1, 0)
        axes_layout.addWidget(self.spin_y_min, 1, 1)
        axes_layout.addWidget(QLabel("to"), 1, 2)
        axes_layout.addWidget(self.spin_y_max, 1, 3)
        axes_layout.addWidget(btn_set_y, 1, 4)
        axes_layout.addWidget(btn_auto_y, 1, 5)
        axes_layout.addWidget(self.chk_fix_y, 1, 6)
        axes_layout.addWidget(self.chk_log_y, 1, 7)
        
        
        btn_set_x.clicked.connect(self.apply_x_range)
        btn_auto_x.clicked.connect(self.auto_x_range)
        self.chk_fix_x.stateChanged.connect(self.toggle_fix_x)
        self.chk_log_x.stateChanged.connect(self.toggle_log_scale)
        
        btn_set_y.clicked.connect(self.apply_y_range)
        btn_auto_y.clicked.connect(self.auto_y_range)
        self.chk_fix_y.stateChanged.connect(self.toggle_fix_y)
        self.chk_log_y.stateChanged.connect(self.toggle_log_scale)
        
        # ------------------------------------------------------------------ EXTRACTION
        # Everything that defines what is measured, in one box: the shape, how its pixels
        # are combined, and its geometry -- expressed the way that shape is actually
        # parameterised. A circle gets a centre and a radius; only a rectangle gets corners.
        # Showing a circle's bounding box, as this group used to, describes the same figure
        # in a form nobody would choose to type.
        group_region = QGroupBox("EXTRACTION APERTURE")
        region_layout = QGridLayout(group_region)
        region_layout.setVerticalSpacing(4)

        self.combo_shape = QComboBox()
        self.combo_shape.addItems(["Rectangle", "Circle"])
        # Circle by default, so the aperture has a centre and a radius and the annulus has
        # something concentric to sit outside.
        self.combo_shape.setCurrentText("Circle")
        self.combo_shape.currentIndexChanged.connect(self.toggle_roi_shape)

        self.combo_calc = QComboBox()
        self.combo_calc.addItems(["Average", "Median", "Total"])
        # Total by default: it is what "aperture photometry with a sky annulus" means, and
        # it makes the background subtraction a single well-defined quantity
        # (aperture_sum - background_level * aperture_area) rather than a per-pixel average
        # whose meaning depends on how many pixels happened to fall in the aperture.
        self.combo_calc.setCurrentText("Total")
        self.combo_calc.currentIndexChanged.connect(self.update_plot)

        region_layout.addWidget(QLabel("Shape:"), 0, 0)
        region_layout.addWidget(self.combo_shape, 0, 1)
        region_layout.addWidget(QLabel("Combine:"), 0, 2)
        region_layout.addWidget(self.combo_calc, 0, 3)

        # --- circle geometry: centre and radius, side by side
        self.spin_cx = QDoubleSpinBox(); self.spin_cy = QDoubleSpinBox()
        self.spin_radius = QDoubleSpinBox()
        for spin in (self.spin_cx, self.spin_cy, self.spin_radius):
            spin.setDecimals(2)
            spin.setSingleStep(0.5)
            spin.setSuffix(" px")
        for spin in (self.spin_cx, self.spin_cy):
            spin.setRange(-10000.0, 10000.0)
            spin.valueChanged.connect(self.on_center_spin_changed)
        self.spin_radius.setRange(0.5, 10000.0)
        self.spin_radius.setValue(DEFAULT_APERTURE_RADIUS)
        self.spin_radius.setToolTip("Aperture radius. Resizes the circle on the image.")
        self.spin_radius.valueChanged.connect(self.on_radius_changed)

        self.geom_circle = QWidget()
        circle_layout = QGridLayout(self.geom_circle)
        circle_layout.setContentsMargins(0, 0, 0, 0)
        circle_layout.addWidget(QLabel("Center X:"), 0, 0)
        circle_layout.addWidget(self.spin_cx, 0, 1)
        circle_layout.addWidget(QLabel("Y:"), 0, 2)
        circle_layout.addWidget(self.spin_cy, 0, 3)
        circle_layout.addWidget(QLabel("Radius:"), 1, 0)
        circle_layout.addWidget(self.spin_radius, 1, 1)
        circle_layout.setColumnStretch(4, 1)
        region_layout.addWidget(self.geom_circle, 1, 0, 1, 4)

        # --- rectangle geometry: the corner spins, unchanged
        self.spin_x0 = QSpinBox(); self.spin_x0.setRange(0, 10000)
        self.spin_x1 = QSpinBox(); self.spin_x1.setRange(0, 10000)
        self.spin_y0 = QSpinBox(); self.spin_y0.setRange(0, 10000)
        self.spin_y1 = QSpinBox(); self.spin_y1.setRange(0, 10000)

        for spin in [self.spin_x0, self.spin_x1, self.spin_y0, self.spin_y1]:
            spin.valueChanged.connect(self.on_spin_changed)

        self.geom_rect = QWidget()
        rect_layout = QGridLayout(self.geom_rect)
        rect_layout.setContentsMargins(0, 0, 0, 0)
        rect_layout.addWidget(QLabel("X Region:"), 0, 0)
        rect_layout.addWidget(self.spin_x0, 0, 1)
        rect_layout.addWidget(QLabel("to"), 0, 2)
        rect_layout.addWidget(self.spin_x1, 0, 3)
        rect_layout.addWidget(QLabel("Y Region:"), 1, 0)
        rect_layout.addWidget(self.spin_y0, 1, 1)
        rect_layout.addWidget(QLabel("to"), 1, 2)
        rect_layout.addWidget(self.spin_y1, 1, 3)
        rect_layout.setColumnStretch(4, 1)
        region_layout.addWidget(self.geom_rect, 2, 0, 1, 4)

        region_layout.setRowStretch(3, 1)

        # Background GroupBox
        self.group_bg = QGroupBox("BACKGROUND")
        bg_layout = QGridLayout(self.group_bg)

        self.chk_enable_bg = QCheckBox("Enable Background Subtraction")
        self.combo_bg_calc = QComboBox()
        self.combo_bg_calc.addItems(["Median", "Average", "Total"])
        self.combo_bg_calc.setCurrentText("Median")
        self.combo_bg_calc.setEnabled(False)

        bg_layout.setVerticalSpacing(4)
        bg_layout.addWidget(self.chk_enable_bg, 0, 0, 1, 2)
        # "Estimator", not a second "Calc using". The label in the EXTRACTION box says how
        # the aperture's pixels are combined; this one says how the sky pixels are reduced
        # to one level. Two different questions should not read as the same control.
        bg_layout.addWidget(QLabel("Estimator:"), 0, 2)
        bg_layout.addWidget(self.combo_bg_calc, 0, 3)

        # Annulus by default. The independent "Region" box remains for a background that
        # has to be measured somewhere specific -- an adjacent slit, a clean corner -- but
        # a sky annulus concentric with the aperture is the usual case and is now the one
        # you get without asking.
        self.combo_bg_mode = QComboBox()
        self.combo_bg_mode.addItems(["Annulus", "Region"])
        self.combo_bg_mode.setCurrentText("Annulus")
        self.combo_bg_mode.setEnabled(False)
        self.combo_bg_mode.currentIndexChanged.connect(self.on_bg_mode_changed)

        self.spin_r_in = QDoubleSpinBox()
        self.spin_r_out = QDoubleSpinBox()
        for spin, value, tip in ((self.spin_r_in, DEFAULT_INNER_RADIUS, "Inner sky radius"),
                                 (self.spin_r_out, DEFAULT_OUTER_RADIUS, "Outer sky radius")):
            spin.setRange(0.5, 10000.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.5)
            spin.setValue(value)
            spin.setSuffix(" px")
            spin.setToolTip(tip)
            spin.valueChanged.connect(self.on_annulus_changed)

        bg_layout.addWidget(QLabel("Mode:"), 1, 0)
        bg_layout.addWidget(self.combo_bg_mode, 1, 1)

        self.spin_bg_x0 = QSpinBox(); self.spin_bg_x0.setRange(0, 10000)
        self.spin_bg_x1 = QSpinBox(); self.spin_bg_x1.setRange(0, 10000)
        self.spin_bg_y0 = QSpinBox(); self.spin_bg_y0.setRange(0, 10000)
        self.spin_bg_y1 = QSpinBox(); self.spin_bg_y1.setRange(0, 10000)

        self._updating_bg_spins = False
        for spin in [self.spin_bg_x0, self.spin_bg_x1, self.spin_bg_y0, self.spin_bg_y1]:
            spin.valueChanged.connect(self.on_bg_spin_changed)

        # The two modes' controls are *swapped*, not greyed out. Disabled spin boxes reading
        # 0 look like measurements; five inert rows were most of this box in every state.
        self.bg_annulus_row = QWidget()
        annulus_layout = QGridLayout(self.bg_annulus_row)
        annulus_layout.setContentsMargins(0, 0, 0, 0)
        annulus_layout.addWidget(QLabel("Sky radii:"), 0, 0)
        annulus_layout.addWidget(self.spin_r_in, 0, 1)
        annulus_layout.addWidget(QLabel("to"), 0, 2)
        annulus_layout.addWidget(self.spin_r_out, 0, 3)
        annulus_layout.setColumnStretch(4, 1)
        bg_layout.addWidget(self.bg_annulus_row, 2, 0, 1, 4)

        self.bg_region_rows = QWidget()
        bg_region_layout = QGridLayout(self.bg_region_rows)
        bg_region_layout.setContentsMargins(0, 0, 0, 0)
        bg_region_layout.addWidget(QLabel("X Region:"), 0, 0)
        bg_region_layout.addWidget(self.spin_bg_x0, 0, 1)
        bg_region_layout.addWidget(QLabel("to"), 0, 2)
        bg_region_layout.addWidget(self.spin_bg_x1, 0, 3)
        bg_region_layout.addWidget(QLabel("Y Region:"), 1, 0)
        bg_region_layout.addWidget(self.spin_bg_y0, 1, 1)
        bg_region_layout.addWidget(QLabel("to"), 1, 2)
        bg_region_layout.addWidget(self.spin_bg_y1, 1, 3)
        bg_region_layout.setColumnStretch(4, 1)
        bg_layout.addWidget(self.bg_region_rows, 3, 0, 1, 4)

        bg_layout.setRowStretch(4, 1)

        # Add the two measurement groups side-by-side
        regions_row_layout = QHBoxLayout()
        regions_row_layout.setContentsMargins(0, 0, 0, 0)
        regions_row_layout.setSpacing(6)
        regions_row_layout.addWidget(group_region)
        regions_row_layout.addWidget(self.group_bg, stretch=1)

        self.layout.addLayout(regions_row_layout)

        # One status line under both groups. It reports the aperture area and annulus pixel
        # count -- which belong to neither box alone -- and is where an unmeasurable geometry
        # is explained, rather than that message hiding inside the background box.
        self.lbl_bg_info = QLabel("")
        self.lbl_bg_info.setWordWrap(True)
        self.lbl_bg_info.setContentsMargins(6, 2, 6, 2)
        # Muted, so a status readout is not mistaken for the heading of the group below it.
        self.lbl_bg_info.setStyleSheet("color: #666;")
        self.layout.addWidget(self.lbl_bg_info)

        # Axis limits are display tuning, not measurement: below the groups that decide
        # what is measured, not between them and the plot.
        self.layout.addWidget(group_axes)

        # Spectral Line List GroupBox in its own row
        self.group_linelist = QGroupBox("SPECTRAL LINE LIST")
        linelist_layout = QGridLayout(self.group_linelist)

        self.chk_enable_lines = QCheckBox("Overplot Line List")
        self.combo_linelist = QComboBox()
        self.btn_browse_linelist = QPushButton("Browse...")
        self.lbl_line_info = QLabel("")

        linelist_layout.addWidget(self.chk_enable_lines, 0, 0)
        linelist_layout.addWidget(QLabel("Line List:"), 0, 1)
        linelist_layout.addWidget(self.combo_linelist, 0, 2)
        linelist_layout.addWidget(self.btn_browse_linelist, 0, 3)
        linelist_layout.addWidget(self.lbl_line_info, 0, 4)

        self.layout.addWidget(self.group_linelist)

        self.line_items = []
        self.loaded_lines = []
        self.linelist_files = {}

        self.chk_enable_lines.stateChanged.connect(self.update_line_overlays)
        self.combo_linelist.currentIndexChanged.connect(self.on_linelist_selection_changed)
        self.btn_browse_linelist.clicked.connect(self.browse_custom_linelist)

        self._updating_spins = False
        self._updating_range_spins = False

        self.populate_linelists()

        # Setup signals for view range changed to update spinboxes
        self.plot_widget.getViewBox().sigXRangeChanged.connect(self.on_x_range_changed)
        self.plot_widget.getViewBox().sigYRangeChanged.connect(self.on_y_range_changed)
        
        if initial_center is not None:
            center_x, center_y = initial_center
        elif self.image_viewer and self.image_viewer.display_data is not None:
            shape = self.image_viewer.display_data.shape
            if len(shape) == 3:
                center_x, center_y = shape[1]//2, shape[2]//2
            else:
                center_x, center_y = shape[0]//2, shape[1]//2
        else:
            center_x, center_y = 2, 2
            
        # Must exist before the first update_plot(), which reads them
        self.bg_roi = None
        self.ring_inner = None
        self.ring_outer = None
        self._updating_radius = False
        self._sky_radii_customised = False

        r = DEFAULT_APERTURE_RADIUS
        self.add_roi_to_viewer(self._build_roi([center_x - r, center_y - r], [r * 2, r * 2]))
        self.sync_control_visibility()
        self.on_roi_changed()

        # Background-subtraction wiring belongs here, not in set_center(): the
        # dialog can be opened without an initial center (Plot -> Depth Plot),
        # and set_center() may be called repeatedly.
        self.chk_enable_bg.stateChanged.connect(self.toggle_background)
        self.combo_bg_calc.currentIndexChanged.connect(self.update_plot)

        self.update_plot()

    @staticmethod
    def _is_drawn(item):
        """True when `item` currently has points on the plot."""
        x, _ = item.getData()
        return x is not None and len(x) > 0

    def sync_legend(self):
        """List exactly the curves that are drawn.

        A fixed three-entry legend describes a plot that is on screen in one state only: with
        the background switched off, and in either cut, two of the three curves are empty, so
        the key names series the user cannot find. Source is permanent; the other two come and
        go with their data.

        Entries are added and removed rather than rebuilt, because `LegendItem.clear()` drops
        the last reference to each `ItemSample` and this file has been bitten by that before
        (`BUGS.md` M18, and `base_tool.remove_roi_from_viewer`).
        """
        listed = {label.text for _, label in self.plot_legend.items}
        for item, name in ((self.plot_bg, "Background"), (self.plot_sub, "Subtracted")):
            drawn = self._is_drawn(item)
            if drawn and name not in listed:
                self.plot_legend.addItem(item, name)
            elif not drawn and name in listed:
                self.plot_legend.removeItem(item)

    def _cut_region(self, plane, x0, x1, y0, y1):
        """The pixels a cut collapses, or None when the aperture is off the plane.

        The cuts used to slice the ROI's *bounding box* whatever shape was selected, so
        `Shape: Circle` measured the corners the circle excludes -- the control stated one
        thing and the arithmetic did another. A cut collapses one axis of the extraction
        aperture, so it is the same aperture the Depth Plot draws.
        """
        region = plane[x0:x1, y0:y1]
        if region.size == 0:
            return None
        mask = self._circular_mask(*region.shape)
        if mask is None:
            return region
        return np.where(mask, region.astype(float), np.nan)

    def _circular_mask(self, nx, ny):
        """Whole pixels of an `nx` by `ny` bounding box whose centres fall inside the circle.

        `None` for a rectangular aperture, so a caller applies the mask or not without
        consulting the shape combo itself. The radius is the half-width of the shorter side,
        matching `aperture_geometry()` -- which is what keeps a circle dragged out of square
        measuring the same figure everywhere it is measured.
        """
        if self.combo_shape.currentText() != "Circle":
            return None
        ix, iy = np.mgrid[:nx, :ny]
        cx, cy = nx / 2.0 - 0.5, ny / 2.0 - 0.5
        r = min(nx / 2.0, ny / 2.0)
        return ((ix - cx) ** 2 + (iy - cy) ** 2) <= r ** 2

    def set_center(self, center):
        center = as_center(center)
        if center is None or self.roi is None:
            return
        cx, cy = center
        w = self.roi.size().x()
        h = self.roi.size().y()
        self.roi.setPos([cx - w / 2.0, cy - h / 2.0])
        self.on_roi_changed()

    def open_export_dialog(self):
        """Open PyQtGraph native export dialog."""
        try:
            from pyqtgraph.GraphicsScene.exportDialog import ExportDialog
            scene = self.plot_widget.scene()
            scene.contextMenuItem = self.plot_widget.plotItem
            if getattr(scene, 'exportDialog', None) is None:
                scene.exportDialog = ExportDialog(scene)
            scene.exportDialog.show(self.plot_widget.plotItem)
        except Exception as e:
            print(f"Error opening export dialog: {e}")
        
    def mouse_moved(self, evt):
        pos = evt[0]
        if self.plot_widget.sceneBoundingRect().contains(pos):
            mousePoint = self.plot_widget.plotItem.vb.mapSceneToView(pos)
            x_val = mousePoint.x()
            y_val = mousePoint.y()
            if hasattr(self, 'current_wavelengths') and self.current_wavelengths is not None and len(self.current_wavelengths) > 0:
                pix_idx = int(round(np.interp(x_val, self.current_wavelengths, np.arange(len(self.current_wavelengths)))))
                unit_str = getattr(self, 'current_wavelength_unit', 'µm')
                self.lbl_cursor.setText(f"Wavelength: {x_val:.4f} {unit_str}  (Pixel: {pix_idx})   Intensity: {y_val:.4f}")
            else:
                self.lbl_cursor.setText(f"Pixel: {x_val:.1f}   Intensity: {y_val:.4f}")
            
    def apply_x_range(self):
        self.plot_widget.setXRange(self.spin_x_min.value(), self.spin_x_max.value(), padding=0)
        self.chk_fix_x.setChecked(True)
        
    def apply_y_range(self):
        self.plot_widget.setYRange(self.spin_y_min.value(), self.spin_y_max.value(), padding=0)
        self.chk_fix_y.setChecked(True)
        
    def auto_x_range(self):
        self.chk_fix_x.setChecked(False)
        self.plot_widget.enableAutoRange(axis=pg.ViewBox.XAxis)
        
    def auto_y_range(self):
        self.chk_fix_y.setChecked(False)
        self.plot_widget.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.plot_widget.getViewBox().autoRange()
        self.update_line_overlays()
        
    def toggle_fix_x(self):
        if self.chk_fix_x.isChecked():
            self.plot_widget.disableAutoRange(axis=pg.ViewBox.XAxis)
        else:
            self.plot_widget.enableAutoRange(axis=pg.ViewBox.XAxis)
            
    def toggle_fix_y(self):
        if self.chk_fix_y.isChecked():
            self.plot_widget.disableAutoRange(axis=pg.ViewBox.YAxis)
        else:
            self.plot_widget.enableAutoRange(axis=pg.ViewBox.YAxis)
            
    def on_x_range_changed(self, _, range_val):
        if not self._updating_range_spins:
            self._updating_range_spins = True
            self.spin_x_min.setValue(range_val[0])
            self.spin_x_max.setValue(range_val[1])
            self._updating_range_spins = False
            self.update_line_overlays()
            
    def on_y_range_changed(self, _, range_val):
        if not self._updating_range_spins:
            self._updating_range_spins = True
            self.spin_y_min.setValue(range_val[0])
            self.spin_y_max.setValue(range_val[1])
            self._updating_range_spins = False
            self.update_line_overlays()

    def toggle_log_scale(self):
        self.plot_widget.setLogMode(x=self.chk_log_x.isChecked(), y=self.chk_log_y.isChecked())
        
    def on_spin_changed(self):
        if self._updating_spins:
            return
        x0 = self.spin_x0.value()
        x1 = self.spin_x1.value()
        y0 = self.spin_y0.value()
        y1 = self.spin_y1.value()
        
        w = max(1, x1 - x0)
        h = max(1, y1 - y0)
        
        self.roi.blockSignals(True)
        self.roi.setPos([x0, y0])
        self.roi.setSize([w, h])
        self.roi.blockSignals(False)
        self.update_plot()
        
    def on_roi_changed(self):
        pos = self.roi.pos()
        size = self.roi.size()
        
        x0, y0 = int(pos.x()), int(pos.y())
        w, h = int(size.x()), int(size.y())
        
        self._updating_spins = True
        self.spin_x0.setValue(x0)
        self.spin_x1.setValue(x0 + w)
        self.spin_y0.setValue(y0)
        self.spin_y1.setValue(y0 + h)
        self._updating_spins = False

        # The aperture radius and the sky rings are derived from the ROI, so every path
        # that moves or resizes it -- a drag, the spin boxes, set_center() from the
        # right-click menu -- arrives here and they follow. That is the whole of "the
        # annulus is not independent of the extraction region".
        geometry = self.aperture_geometry()
        if geometry is not None:
            self._updating_radius = True
            self.spin_cx.setValue(geometry[0])
            self.spin_cy.setValue(geometry[1])
            self.spin_radius.setValue(geometry[2])
            self.track_aperture_radii(geometry[2])
            self._updating_radius = False
        self.sync_annulus_rings()

        self.update_plot()

    # ------------------------------------------------------------ circular aperture

    def _build_roi(self, pos, size):
        """The source ROI for the currently selected shape."""
        pen = pg.mkPen((0, 255, 0), width=3)
        hover = pg.mkPen((0, 255, 0), width=5)
        if self.combo_shape.currentText() == "Circle":
            return pg.CircleROI(pos, size, pen=pen, hoverPen=hover)
        roi = pg.RectROI(pos, size, pen=pen, hoverPen=hover)
        roi.addScaleHandle([1, 1], [0, 0])
        roi.addScaleHandle([0, 0], [1, 1])
        return roi

    def aperture_geometry(self):
        """`(cx, cy, radius)` of the source ROI, in display pixels, or None without one.

        The radius is the half-width of the shorter side, so a circle dragged out of square
        still measures a circle -- the same rule `update_plot`'s rectangle path uses for its
        mask, and the reason the two agree.
        """
        if self.roi is None:
            return None
        pos, size = self.roi.pos(), self.roi.size()
        cx = pos.x() + size.x() / 2.0
        cy = pos.y() + size.y() / 2.0
        return cx, cy, min(size.x(), size.y()) / 2.0

    def annulus_is_active(self):
        """True when the background comes from a sky annulus rather than a free region."""
        return (self.chk_enable_bg.isChecked()
                and self.combo_bg_mode.currentText() == "Annulus"
                and self.combo_shape.currentText() == "Circle")

    def on_radius_changed(self):
        """Resize the source ROI about its centre to match the radius spin box."""
        if self._updating_radius or self.roi is None:
            return
        geometry = self.aperture_geometry()
        if geometry is None:
            return
        cx, cy, current = geometry
        r = self.spin_radius.value()
        if abs(r - current) < 1e-6:
            return
        self.roi.blockSignals(True)
        self.roi.setPos([cx - r, cy - r])
        self.roi.setSize([r * 2, r * 2])
        self.roi.blockSignals(False)
        # The ROI's signals are blocked, so on_roi_changed() will not run and this path has
        # to carry the annulus along itself.
        self._updating_radius = True
        self.track_aperture_radii(r)
        self._updating_radius = False
        self.sync_annulus_rings()
        self.update_plot()

    def sync_control_visibility(self):
        """Show the controls that describe the current shape and background mode.

        Swapped rather than disabled: a greyed spin box still reads as a number someone
        measured, and in every state roughly half of these controls do not apply.
        """
        circle = self.combo_shape.currentText() == "Circle"
        self.geom_circle.setVisible(circle)
        self.geom_rect.setVisible(not circle)

        enabled = self.chk_enable_bg.isChecked()
        annulus = self.annulus_is_active()
        self.bg_annulus_row.setVisible(enabled and annulus)
        self.bg_region_rows.setVisible(enabled and not annulus)

    def on_center_spin_changed(self):
        """Move the aperture to a typed centre."""
        if self._updating_radius or self.roi is None:
            return
        self.set_center((self.spin_cx.value(), self.spin_cy.value()))

    def track_aperture_radii(self, r_ap):
        """Move the sky radii to sit just outside an aperture of `r_ap`.

        Only until the user sets one themselves: after that the numbers they typed are the
        ones that stay, because someone who has chosen an annulus has chosen it for a reason.
        """
        if self._sky_radii_customised:
            return
        self.spin_r_in.setValue(r_ap + SKY_INNER_GAP)
        self.spin_r_out.setValue(r_ap + SKY_INNER_GAP + SKY_WIDTH)

    def on_annulus_changed(self):
        """Keep the two sky radii ordered, then redraw."""
        if self._updating_radius:
            return
        # Reached only on a genuine edit -- every programmatic write is inside the guard --
        # so this is where the annulus stops following the aperture.
        self._sky_radii_customised = True
        # An inner radius at or beyond the outer one is not a ring; push the outer one out
        # rather than refusing the edit, so typing into either box always does something.
        if self.spin_r_out.value() <= self.spin_r_in.value():
            self._updating_radius = True
            self.spin_r_out.setValue(self.spin_r_in.value() + self.spin_r_out.singleStep())
            self._updating_radius = False
        self.sync_annulus_rings()
        self.update_plot()

    def add_annulus_rings(self):
        """Two concentric guides on the image. Not draggable: the aperture places them."""
        self.remove_annulus_rings()
        if self.image_viewer is None or getattr(self.image_viewer, 'imv', None) is None:
            return

        pen = pg.mkPen(_RING_COLOR, width=2, style=Qt.DashLine)
        hover = pg.mkPen(_RING_COLOR, width=2, style=Qt.DashLine)
        img_item = self.image_viewer.imv.getImageItem()
        for name in ("ring_inner", "ring_outer"):
            ring = pg.CircleROI([0, 0], [1, 1], pen=pen, hoverPen=hover,
                                movable=False, resizable=False)
            # The lone handle would offer a resize the model does not support.
            try:
                ring.removeHandle(0)
            except Exception:
                pass
            if img_item is not None:
                ring.setParentItem(img_item)
            else:
                self.image_viewer.imv.getView().addItem(ring)
            setattr(self, name, ring)
        self.sync_annulus_rings()

    def remove_annulus_rings(self):
        """Take the rings off the scene.

        `removeItem` on the ViewBox, not `setParentItem(None)`: a parented item detached
        that way stays painted (`BUGS.md` B7).
        """
        for name in ("ring_inner", "ring_outer"):
            ring = getattr(self, name, None)
            if ring is None:
                continue
            if self.image_viewer is not None:
                try:
                    self.image_viewer.imv.getView().removeItem(ring)
                except Exception:
                    pass
                try:
                    ring.setParentItem(None)
                except Exception:
                    pass
            setattr(self, name, None)

    def sync_annulus_rings(self):
        """Centre the rings on the aperture and size them from the two radius spins."""
        if self.ring_inner is None or self.ring_outer is None:
            return
        geometry = self.aperture_geometry()
        if geometry is None:
            return
        cx, cy, _ = geometry
        for ring, radius in ((self.ring_inner, self.spin_r_in.value()),
                             (self.ring_outer, self.spin_r_out.value())):
            ring.setPos([cx - radius, cy - radius])
            ring.setSize([radius * 2, radius * 2])

    def region_background_level(self, cube):
        """Per-channel background from the independent region ROI, or None.

        A circular aperture can still take its background from a box drawn elsewhere -- an
        adjacent clean patch of sky -- so this exists for `Region` mode and returns the same
        kind of per-channel level the annulus produces, to be applied by the same rule.
        """
        if self.bg_roi is None:
            return None
        _, x_len, y_len = cube.shape
        pos, size = self.bg_roi.pos(), self.bg_roi.size()
        x0, y0 = int(pos.x()), int(pos.y())
        w, h = int(size.x()), int(size.y())

        x0 = max(0, min(x0, x_len - 1))
        y0 = max(0, min(y0, y_len - 1))
        x1 = max(x0 + 1, min(x0 + w, x_len))
        y1 = max(y0 + 1, min(y0 + h, y_len))

        region = cube[:, x0:x1, y0:y1].astype(float, copy=True)
        if region.size == 0:
            return None

        if self.combo_shape.currentText() == "Circle":
            yy, xx = np.mgrid[:(x1 - x0), :(y1 - y0)]
            cx, cy = (x1 - x0) / 2.0 - 0.5, (y1 - y0) / 2.0 - 0.5
            r = min((x1 - x0) / 2.0, (y1 - y0) / 2.0)
            region = np.where(((xx - cy) ** 2 + (yy - cx) ** 2) <= r ** 2, region, np.nan)

        method = self.combo_bg_calc.currentText()
        with np.errstate(invalid="ignore"):
            if method == "Average":
                return np.nanmean(region, axis=(1, 2))
            if method == "Median":
                return np.nanmedian(region, axis=(1, 2))
            return np.nansum(region, axis=(1, 2))

    def on_bg_mode_changed(self):
        """Swap between a concentric annulus and an independent region."""
        if not self.chk_enable_bg.isChecked():
            return
        self.toggle_background()

    def add_bg_roi(self):
        if self.bg_roi is not None:
            self.remove_bg_roi()

        if self.image_viewer is None or getattr(self.image_viewer, 'imv', None) is None:
            return

        shape = self.combo_shape.currentText()
        pos = self.roi.pos() if self.roi else [0, 0]
        size = self.roi.size() if self.roi else [4, 4]

        # Offset background ROI by width + 2 pixels
        bg_pos = [pos.x() + size.x() + 2, pos.y()]

        pen = pg.mkPen((255, 140, 0), width=3)
        hover_pen = pg.mkPen((255, 140, 0), width=5)

        if shape == "Circle":
            self.bg_roi = pg.CircleROI(bg_pos, size, pen=pen, hoverPen=hover_pen)
        else:
            self.bg_roi = pg.RectROI(bg_pos, size, pen=pen, hoverPen=hover_pen)
            self.bg_roi.addScaleHandle([1, 1], [0, 0])
            self.bg_roi.addScaleHandle([0, 0], [1, 1])

        img_item = self.image_viewer.imv.getImageItem()
        if img_item:
            self.bg_roi.setParentItem(img_item)
        else:
            self.image_viewer.imv.getView().addItem(self.bg_roi)
        self.bg_roi.sigRegionChanged.connect(self.on_bg_roi_changed)
        self.on_bg_roi_changed()

    def remove_bg_roi(self):
        if self.bg_roi is not None and self.image_viewer is not None:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    self.bg_roi.sigRegionChanged.disconnect(self.on_bg_roi_changed)
                except Exception:
                    pass
            try:
                self.bg_roi.setParentItem(None)
            except Exception:
                pass
            try:
                self.image_viewer.imv.getView().removeItem(self.bg_roi)
            except Exception:
                pass
            self.bg_roi = None

    def toggle_background(self, state=None):
        """Enable background subtraction, in whichever mode is selected.

        The two modes are mutually exclusive on screen as well as in the arithmetic: an
        annulus has no independent position, so its box spins are meaningless and the free
        region ROI must not be left on the image beside it.
        """
        checked = self.chk_enable_bg.isChecked()
        annulus = checked and self.annulus_is_active()

        if checked and not annulus:
            if self.bg_roi is None:
                self.add_bg_roi()
        else:
            self.remove_bg_roi()

        if annulus:
            if self.ring_inner is None:
                self.add_annulus_rings()
            else:
                self.sync_annulus_rings()
        else:
            self.remove_annulus_rings()

        self.combo_bg_calc.setEnabled(checked)
        self.combo_bg_mode.setEnabled(checked)
        self.sync_control_visibility()

        # A background *total* means nothing when it is subtracted per pixel -- the number
        # would scale with however wide the annulus was drawn. Offer it only for the region
        # mode, which predates this and where users may rely on it.
        total_index = self.combo_bg_calc.findText("Total")
        if total_index >= 0:
            item = self.combo_bg_calc.model().item(total_index)
            if item is not None:
                item.setEnabled(not annulus)
            if annulus and self.combo_bg_calc.currentText() == "Total":
                self.combo_bg_calc.setCurrentText("Median")

        self.update_plot()

    def on_bg_roi_changed(self):
        if self.bg_roi is None:
            return
        pos = self.bg_roi.pos()
        size = self.bg_roi.size()

        x0, y0 = int(pos.x()), int(pos.y())
        w, h = int(size.x()), int(size.y())

        self._updating_bg_spins = True
        self.spin_bg_x0.setValue(x0)
        self.spin_bg_x1.setValue(x0 + w)
        self.spin_bg_y0.setValue(y0)
        self.spin_bg_y1.setValue(y0 + h)
        self._updating_bg_spins = False

        self.update_plot()

    def on_bg_spin_changed(self):
        if getattr(self, '_updating_bg_spins', False) or self.bg_roi is None:
            return
        x0 = self.spin_bg_x0.value()
        x1 = self.spin_bg_x1.value()
        y0 = self.spin_bg_y0.value()
        y1 = self.spin_bg_y1.value()

        w = max(1, x1 - x0)
        h = max(1, y1 - y0)

        self.bg_roi.blockSignals(True)
        self.bg_roi.setPos([x0, y0])
        self.bg_roi.setSize([w, h])
        self.bg_roi.blockSignals(False)
        self.update_plot()

    def closeEvent(self, event):
        self.clear_line_overlays()
        self.remove_bg_roi()
        self.remove_annulus_rings()
        super().closeEvent(event)

    def get_data_dir(self):
        import sys
        import pyql3

        candidates = []
        if hasattr(sys, '_MEIPASS'):
            candidates.append(pathlib.Path(sys._MEIPASS) / "pyql3" / "data")
            candidates.append(pathlib.Path(sys._MEIPASS) / "data")

        try:
            from pyql3 import get_resource_path
            candidates.append(pathlib.Path(get_resource_path("pyql3/data")))
        except Exception:
            pass

        pyql3_dir = pathlib.Path(pyql3.__file__).resolve().parent
        candidates.append(pyql3_dir / "data")

        cur_dir = pathlib.Path(__file__).resolve().parent
        candidates.append(cur_dir.parents[1] / "data")
        candidates.append(cur_dir.parents[2] / "data")

        for cand in candidates:
            if cand.exists() and cand.is_dir():
                return cand

        return pyql3_dir / "data"

    def add_linelist_item(self, filepath, index=None):
        """Put one line list in the combo, under its own filename.

        The filename *is* the name: a line list is data, and prettifying `nir_stellar_lines.txt`
        into "NIR Stellar Lines" breaks the association with the file on disk that the numbers
        can be checked against. The full path rides along in the item data and tooltip, which
        says which *copy* was loaded -- a frozen bundle, the repo, a browsed file -- without
        changing what is displayed.
        """
        name = os.path.basename(filepath)
        self.linelist_files[name] = filepath
        if index is None:
            self.combo_linelist.addItem(name, filepath)
            index = self.combo_linelist.count() - 1
        else:
            self.combo_linelist.insertItem(index, name, filepath)
        self.combo_linelist.setItemData(index, filepath, Qt.ToolTipRole)
        return index

    def populate_linelists(self):
        self.combo_linelist.blockSignals(True)
        self.combo_linelist.clear()
        self.linelist_files.clear()

        data_dir = self.get_data_dir()
        if data_dir.exists():
            for p in sorted(data_dir.glob("*")):
                if p.suffix.lower() in [".txt", ".csv"]:
                    self.add_linelist_item(str(p))

        self.combo_linelist.addItem("Load Custom CSV...")

        default = ("nir_stellar_lines.txt" if "nir_stellar_lines.txt" in self.linelist_files
                   else next(iter(self.linelist_files), None))

        if default is not None:
            self.combo_linelist.setCurrentText(default)
            self.loaded_lines = self.parse_line_list(self.linelist_files[default])
        else:
            self.combo_linelist.setCurrentText("Load Custom CSV...")
            self.loaded_lines = []

        self.combo_linelist.blockSignals(False)

    def parse_line_list(self, filepath):
        lines = []
        if not filepath or not os.path.exists(filepath):
            return lines
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith(';'):
                        continue
                    parts = line.split(',')
                    if len(parts) >= 2:
                        try:
                            wl = float(parts[0].strip())
                            name = parts[1].strip()
                            lines.append((wl, name))
                        except ValueError:
                            continue
        except Exception as e:
            print(f"Error parsing line list {filepath}: {e}")
        return lines

    def on_linelist_selection_changed(self):
        text = self.combo_linelist.currentText()
        if text in self.linelist_files:
            filepath = self.linelist_files[text]
            self.loaded_lines = self.parse_line_list(filepath)
            self.update_line_overlays()

    def browse_custom_linelist(self):
        data_dir = self.get_data_dir()
        if data_dir and data_dir.exists():
            initial_dir = str(data_dir)
        else:
            initial_dir = QDir.homePath()

        filepath, _ = QFileDialog.getOpenFileName(
            self, "Select Spectral Line List CSV", initial_dir, "CSV / Text Files (*.csv *.txt);;All Files (*)"
        )
        if filepath:
            idx = self.combo_linelist.findText("Load Custom CSV...")
            idx = self.add_linelist_item(filepath, index=idx if idx >= 0 else None)
            self.combo_linelist.setCurrentIndex(idx)
            self.loaded_lines = self.parse_line_list(filepath)
            self.update_line_overlays()
        else:
            if self.combo_linelist.currentText() == "Load Custom CSV..." and self.combo_linelist.count() > 1:
                self.combo_linelist.setCurrentIndex(0)

    def wavelength_to_pixel(self, wavelengths_um):
        if self.image_viewer is None or getattr(self.image_viewer, 'wcs', None) is None:
            return None
        if getattr(self.image_viewer, 'wcs_z_idx', None) is None:
            return None

        wcs = self.image_viewer.wcs
        z_idx = self.image_viewer.wcs_z_idx

        try:
            cunit = str(wcs.wcs.cunit[z_idx]).strip().lower()
        except Exception:
            cunit = "m"

        if cunit == 'm':
            scale = 1e-6
        elif cunit in ['um', 'micron', 'microns', 'µm']:
            scale = 1.0
        elif cunit == 'nm':
            scale = 1e3
        elif cunit in ['angstrom', 'a', 'angstroms']:
            scale = 1e4
        else:
            scale = 1e-6

        wls_wcs = np.array(wavelengths_um) * scale
        n_lines = len(wls_wcs)
        coords_world = np.zeros((n_lines, wcs.naxis))

        if hasattr(self, 'world_axis') and self.world_axis.fixed_coords is not None:
            for i in range(wcs.naxis):
                if i == z_idx:
                    coords_world[:, i] = wls_wcs
                else:
                    coords_world[:, i] = self.world_axis.fixed_coords[i]
        else:
            ref_pix = np.zeros((1, wcs.naxis))
            ref_world = wcs.wcs_pix2world(ref_pix, 0)[0]
            for i in range(wcs.naxis):
                if i == z_idx:
                    coords_world[:, i] = wls_wcs
                else:
                    coords_world[:, i] = ref_world[i]

        try:
            pix_coords = wcs.wcs_world2pix(coords_world, 0)[:, z_idx]
            return pix_coords
        except Exception:
            return None

    def clear_line_overlays(self):
        for item in self.line_items:
            try:
                if isinstance(item, tuple):
                    line_item, text_item = item
                    self.plot_widget.removeItem(line_item)
                    self.plot_widget.removeItem(text_item)
                else:
                    self.plot_widget.removeItem(item)
            except Exception:
                pass
        self.line_items.clear()

    def update_line_overlays(self):
        if not hasattr(self, 'chk_enable_lines'):
            return

        plot_type = self.combo_type.currentText()
        lines_enabled = self.chk_enable_lines.isChecked() and (plot_type == "Depth Plot")

        if not lines_enabled or not self.loaded_lines or self.image_viewer is None or getattr(self.image_viewer, 'wcs', None) is None:
            self.clear_line_overlays()
            if hasattr(self, 'lbl_line_info'):
                if not self.chk_enable_lines.isChecked():
                    self.lbl_line_info.setText("")
                elif plot_type != "Depth Plot":
                    self.lbl_line_info.setText("Line list only available in Depth Plot mode.")
                elif getattr(self.image_viewer, 'wcs', None) is None:
                    self.lbl_line_info.setText("No WCS present for wavelength mapping.")
            return

        z_idx = getattr(self.image_viewer, 'wcs_z_idx', None)
        if z_idx is not None:
            ctype_raw = str(self.image_viewer.wcs.wcs.ctype[z_idx]).upper()
            if 'WAVE' not in ctype_raw and 'AWAV' not in ctype_raw:
                self.clear_line_overlays()
                self.lbl_line_info.setText("Z-axis is not Wavelength.")
                return

        use_wavelength_x = hasattr(self, 'current_wavelengths') and self.current_wavelengths is not None and len(self.current_wavelengths) > 0

        view_box = self.plot_widget.getViewBox()
        (view_x_min, view_x_max), (view_y_min, view_y_max) = view_box.viewRange()

        if view_x_min == 0.0 and view_x_max == 1.0 and hasattr(self, 'plot_data'):
            x_data, _ = self.plot_data.getData()
            if x_data is not None and len(x_data) > 1:
                view_x_min, view_x_max = float(x_data[0]), float(x_data[-1])

        visible_lines = []
        if use_wavelength_x:
            for wl_um, name in self.loaded_lines:
                if view_x_min <= wl_um <= view_x_max:
                    visible_lines.append((wl_um, name, wl_um))
        else:
            wls_um = [item[0] for item in self.loaded_lines]
            pix_coords = self.wavelength_to_pixel(wls_um)
            if pix_coords is None:
                self.clear_line_overlays()
                self.lbl_line_info.setText("WCS conversion failed.")
                return
            # strict=: pix_coords is derived one-for-one from loaded_lines, so a
            # length mismatch is a bug, not something to silently truncate.
            for (wl_um, name), x_px in zip(self.loaded_lines, pix_coords, strict=True):
                if view_x_min <= x_px <= view_x_max:
                    visible_lines.append((x_px, name, wl_um))

        num_needed = len(visible_lines)

        while len(self.line_items) > num_needed:
            item = self.line_items.pop()
            try:
                if isinstance(item, tuple):
                    line_item, text_item = item
                    self.plot_widget.removeItem(line_item)
                    self.plot_widget.removeItem(text_item)
                else:
                    self.plot_widget.removeItem(item)
            except Exception:
                pass

        pen = pg.mkPen(color=(0, 100, 220), style=Qt.PenStyle.DotLine, width=1.5)
        last_x = -9999.0
        level = 0
        y_span = view_y_max - view_y_min
        min_spacing = 0.005 * (view_x_max - view_x_min) if use_wavelength_x else 18.0

        for idx, (x_pos, name, _wl_um) in enumerate(visible_lines):
            if abs(x_pos - last_x) < min_spacing:
                level = (level + 1) % len(GUTTER_LEVELS)
            else:
                level = 0
            last_x = x_pos

            y_pos = view_y_min + GUTTER_LEVELS[level] * y_span

            html_content = latex_to_html(name)
            html_text = f'<span style="color: rgb(0, 70, 180); font-size: 12pt; font-weight: bold;">{html_content}</span>'

            if idx < len(self.line_items):
                line_item, text_item = self.line_items[idx]
                line_item.setPos(x_pos)
                line_item.setVisible(True)

                text_item.setHtml(html_text)
                text_item.setAngle(90)
                text_item.setPos(x_pos, y_pos)
                text_item.setVisible(True)
            else:
                line_item = pg.InfiniteLine(pos=x_pos, angle=90, pen=pen)
                # anchor=(0.0, 0.5) with setAngle(90) stands the name *up* from its row,
                # which is what a row near the floor needs. (1.0, 0.5) hangs it downwards
                # instead -- the two are not interchangeable if the rows ever move.
                text_item = pg.TextItem(html=html_text, anchor=(0.0, 0.5))
                line_item.dataBounds = lambda ax, *args, **kwargs: (None, None)
                text_item.dataBounds = lambda ax, *args, **kwargs: (None, None)
                text_item.setAngle(90)
                text_item.setPos(x_pos, y_pos)

                self.plot_widget.addItem(line_item)
                self.plot_widget.addItem(text_item)
                self.line_items.append((line_item, text_item))

        self.lbl_line_info.setText(f"{len(visible_lines)} line(s) visible (out of {len(self.loaded_lines)} total)")
        
    def toggle_roi_shape(self):
        shape = self.combo_shape.currentText()
        pos = self.roi.pos()
        size = self.roi.size()
        
        self.remove_roi_from_viewer()
        
        if shape == "Circle":
            roi = pg.CircleROI(pos, size, pen=pg.mkPen((0, 255, 0), width=3), hoverPen=pg.mkPen((0, 255, 0), width=5))
        else:
            roi = pg.RectROI(pos, size, pen=pg.mkPen((0, 255, 0), width=3), hoverPen=pg.mkPen((0, 255, 0), width=5))
            roi.addScaleHandle([1, 1], [0, 0])
            roi.addScaleHandle([0, 0], [1, 1])
            
        self.add_roi_to_viewer(roi)

        if self.chk_enable_bg.isChecked() and self.bg_roi is not None:
            bg_pos = self.bg_roi.pos()
            bg_size = self.bg_roi.size()
            self.remove_bg_roi()
            pen = pg.mkPen((255, 140, 0), width=3)
            hover_pen = pg.mkPen((255, 140, 0), width=5)
            if shape == "Circle":
                self.bg_roi = pg.CircleROI(bg_pos, bg_size, pen=pen, hoverPen=hover_pen)
            else:
                self.bg_roi = pg.RectROI(bg_pos, bg_size, pen=pen, hoverPen=hover_pen)
                self.bg_roi.addScaleHandle([1, 1], [0, 0])
                self.bg_roi.addScaleHandle([0, 0], [1, 1])
            img_item = self.image_viewer.imv.getImageItem()
            if img_item:
                self.bg_roi.setParentItem(img_item)
            else:
                self.image_viewer.imv.getView().addItem(self.bg_roi)
            self.bg_roi.sigRegionChanged.connect(self.on_bg_roi_changed)

        # An annulus needs a circle to be concentric with, so over a rectangle the option is
        # *withdrawn*, not quietly ignored. Leaving the combo reading "Annulus" while the
        # tool measured a free region was a control that stated the opposite of what it did.
        circle = shape == "Circle"
        index = self.combo_bg_mode.findText("Annulus")
        item = self.combo_bg_mode.model().item(index) if index >= 0 else None
        if item is not None:
            item.setEnabled(circle)

        if not circle and self.combo_bg_mode.currentText() == "Annulus":
            self._forced_region = True
            self.combo_bg_mode.setCurrentText("Region")   # re-enters toggle_background
        elif circle and getattr(self, '_forced_region', False):
            # Only restore what this took away; a Region the user chose stays chosen.
            self._forced_region = False
            self.combo_bg_mode.setCurrentText("Annulus")
        elif self.chk_enable_bg.isChecked():
            self.toggle_background()

        # Also when the background is off: the geometry row still has to follow the shape.
        self.sync_control_visibility()
        self.update_plot()

    def _draw_depth_curves(self, spectrum, bg_spectrum, subtracted_spectrum, z_len,
                           center):
        """Put the three spectra on the plot, against a wavelength axis when there is one.

        Shared by the circular and rectangular extraction paths so that the WCS lookup,
        the axis labelling and the DN/s -> Total DN multiplier are defined once. `center`
        is the aperture centre in display pixels, which is what the wavelength solution is
        evaluated at.
        """
        x_axis = np.arange(z_len)
        wavelengths = None
        cunit = ""
        ctype = ""
        
        if self.image_viewer.wcs is not None and self.image_viewer.wcs_z_idx is not None:
            wcs = self.image_viewer.wcs
            z_idx = self.image_viewer.wcs_z_idx
            ctype_raw = str(wcs.wcs.ctype[z_idx]).upper()
            ctype = ctype_raw.split('-')[0] if '-' in ctype_raw else ctype_raw
            
            try:
                cunit = str(wcs.wcs.cunit[z_idx]).strip()
                if cunit.lower() == 'm':
                    cunit = 'µm'
            except Exception:
                cunit = "µm"
            
            cx, cy = center

            # Un-flip and un-rotate to get coords in transposed_data space. This used to
            # be inlined here with one axis length used for both axes, which put the
            # lookup at the wrong pixel for a rotated non-square plane (BUGS.md B13).
            cx, cy = self.image_viewer.display_to_orig(cx, cy)

            x_idx, y_idx = self.image_viewer.display_axis_indices()
                
            fixed_coords = np.zeros((z_len, wcs.naxis))
            if wcs.naxis > max(x_idx, y_idx):
                fixed_coords[:, x_idx] = cx
                fixed_coords[:, y_idx] = cy
            fixed_coords[:, z_idx] = np.arange(z_len)

            try:
                world = wcs.wcs_pix2world(fixed_coords, 0)
                wavelengths = world[:, z_idx]
                try:
                    orig_cunit = str(wcs.wcs.cunit[z_idx]).strip().lower()
                    if orig_cunit == 'm':
                        wavelengths = wavelengths * 1e6
                except Exception:
                    pass
            except Exception as e:
                print(f"Warning: WCS pixel_to_world failed in DepthPlotDialog: {e}")
                wavelengths = None

        if wavelengths is not None and len(wavelengths) == z_len:
            x_axis = wavelengths
            self.current_wavelengths = wavelengths
            self.current_wavelength_unit = cunit
            
            label = "Wavelength" if 'WAVE' in ctype else ctype
            unit_str = f" ({cunit})" if cunit else ""
            self.plot_widget.getAxis('bottom').setLabel(f"{label}{unit_str}")
            
            self.top_axis.wavelengths = wavelengths
            self.plot_widget.showAxis('top')
            self.plot_widget.getAxis('top').setLabel("Slice Index (pixels)")
        else:
            x_axis = np.arange(z_len)
            self.current_wavelengths = None
            self.current_wavelength_unit = ""
            self.plot_widget.setLabel('bottom', "Slice Index (pixels)")
            self.top_axis.wavelengths = None
            self.plot_widget.hideAxis('top')
        
        mult = self.image_viewer.data_multiplier
        self.plot_data.setData(x_axis, spectrum * mult)

        if bg_spectrum is not None and subtracted_spectrum is not None:
            self.plot_bg.setData(x_axis, bg_spectrum * mult)
            self.plot_sub.setData(x_axis, subtracted_spectrum * mult)
        else:
            self.plot_bg.setData([], [])
            self.plot_sub.setData([], [])

        self.sync_legend()

    def update_plot(self):
        if self.image_viewer is None or self.image_viewer.transposed_data is None:
            return
            
        if self.image_viewer.transposed_data.ndim != 3:
            return
            
        plot_type = self.combo_type.currentText()
        calc_method = self.combo_calc.currentText()

        # Cleared here and nowhere else, so no readout can outlive the measurement it
        # describes. `Aperture 28.3 px²` used to survive a switch to a cut, which performs no
        # aperture photometry at all, and sat under the plot describing numbers it had no
        # part in. Each branch below either replaces this line or means to leave it empty.
        self.lbl_bg_info.setText("")

        depth = plot_type == "Depth Plot"
        if hasattr(self, 'group_bg'):
            self.group_bg.setEnabled(depth)
        if hasattr(self, 'group_linelist'):
            self.group_linelist.setEnabled(depth)
        if not depth:
            # The reason a group is inert has to be readable, and `lbl_line_info` lives
            # *inside* the line-list group, so it was greyed out along with everything it
            # explained. The status line is outside both groups.
            self.lbl_bg_info.setText(
                "Background subtraction and line lists apply to the Depth Plot only.")


        # Transform the 3D cube to match the display coordinates (rotation, flip)
        cube = self.image_viewer.apply_spatial_transforms(self.image_viewer.transposed_data)

        pos = self.roi.pos()
        size = self.roi.size()
        
        x0, y0 = int(pos.x()), int(pos.y())
        w, h = int(size.x()), int(size.y())
        
        shape = cube.shape
        z_len, x_len, y_len = shape
        
        x0 = max(0, min(x0, x_len-1))
        y0 = max(0, min(y0, y_len-1))
        x1 = max(x0+1, min(x0+w, x_len))
        y1 = max(y0+1, min(y0+h, y_len))
        
        if plot_type == "Depth Plot" and self.combo_shape.currentText() == "Circle":
            # The circular path goes through pyql3/core/spectral_photometry.py so that the
            # aperture is defined once -- with fractional edge pixels -- and means the same
            # thing whether or not a background is being subtracted. See that module for
            # what Total/Average/Median mean once a background level is involved.
            spectrum, bg_spectrum, subtracted_spectrum = None, None, None
            geometry = self.aperture_geometry()
            annulus = self.annulus_is_active()
            try:
                if geometry is None:
                    raise ApertureError("no aperture on the image")
                cx, cy, r_ap = geometry
                result = annulus_spectrum(
                    cube, (cx, cy), r_ap,
                    r_in=self.spin_r_in.value() if annulus else None,
                    r_out=self.spin_r_out.value() if annulus else None,
                    combine=calc_method,
                    estimator=self.combo_bg_calc.currentText() if annulus else "Median")
            except ApertureError as exc:
                # A geometry that cannot be measured is reported, not drawn as a flat line
                # that would be read as data.
                self.lbl_bg_info.setText(f"<span style='color:#b00'>{exc}</span>")
                self.plot_data.setData([], [])
                self.plot_bg.setData([], [])
                self.plot_sub.setData([], [])
                self.sync_legend()
                return

            spectrum, bg_spectrum, subtracted_spectrum = result.as_tuple()
            if annulus:
                self.lbl_bg_info.setText(
                    f"Aperture {result.aperture_area:.1f} px², "
                    f"sky annulus {result.annulus_pixels} px")
            elif self.chk_enable_bg.isChecked():
                # Circular aperture, background from the independent region. Applied by the
                # same rule as the annulus so the two modes differ only in where the level
                # was measured.
                bg_spectrum = self.region_background_level(cube)
                if bg_spectrum is not None:
                    subtracted_spectrum = subtract_background(
                        spectrum, bg_spectrum, calc_method, result.aperture_area)
                self.lbl_bg_info.setText(f"Aperture {result.aperture_area:.1f} px²")
            else:
                self.lbl_bg_info.setText("")

            self._draw_depth_curves(spectrum, bg_spectrum, subtracted_spectrum, z_len,
                                    (cx, cy))
            return

        if plot_type == "Depth Plot":
            region = cube[:, x0:x1, y0:y1].astype(float, copy=True)
            if region.size == 0:
                return

            mask = self._circular_mask(x1 - x0, y1 - y0)
            if mask is not None:
                region = np.where(mask, region, np.nan)


            if calc_method == "Average":
                spectrum = np.nanmean(region, axis=(1, 2))
            elif calc_method == "Median":
                spectrum = np.nanmedian(region, axis=(1, 2))
            else:
                spectrum = np.nansum(region, axis=(1, 2))

            bg_spectrum = None
            subtracted_spectrum = None

            if self.chk_enable_bg.isChecked() and self.bg_roi is not None:
                bg_pos = self.bg_roi.pos()
                bg_size = self.bg_roi.size()
                bg_x0, bg_y0 = int(bg_pos.x()), int(bg_pos.y())
                bg_w, bg_h = int(bg_size.x()), int(bg_size.y())

                bg_x0 = max(0, min(bg_x0, x_len-1))
                bg_y0 = max(0, min(bg_y0, y_len-1))
                bg_x1 = max(bg_x0+1, min(bg_x0+bg_w, x_len))
                bg_y1 = max(bg_y0+1, min(bg_y0+bg_h, y_len))

                bg_region = cube[:, bg_x0:bg_x1, bg_y0:bg_y1].astype(float, copy=True)
                if bg_region.size > 0:
                    mask_bg = self._circular_mask(bg_x1 - bg_x0, bg_y1 - bg_y0)
                    if mask_bg is not None:
                        bg_region = np.where(mask_bg, bg_region, np.nan)

                    bg_calc_method = self.combo_bg_calc.currentText()
                    if bg_calc_method == "Average":
                        bg_spectrum = np.nanmean(bg_region, axis=(1, 2))
                    elif bg_calc_method == "Median":
                        bg_spectrum = np.nanmedian(bg_region, axis=(1, 2))
                    else:
                        bg_spectrum = np.nansum(bg_region, axis=(1, 2))

                    # Option B: Subtract background spectrum from each pixel's spectrum in the source data
                    subtracted_region = region - bg_spectrum[:, None, None]

                    if calc_method == "Average":
                        subtracted_spectrum = np.nanmean(subtracted_region, axis=(1, 2))
                    elif calc_method == "Median":
                        subtracted_spectrum = np.nanmedian(subtracted_region, axis=(1, 2))
                    else:
                        subtracted_spectrum = np.nansum(subtracted_region, axis=(1, 2))
                
            self._draw_depth_curves(spectrum, bg_spectrum, subtracted_spectrum, z_len,
                                    (x0 + w / 2.0, y0 + h / 2.0))

        elif plot_type == "Horizontal Cut":
            # Cut the plane that is actually on screen: in Boxcar or Z Range mode that is a
            # collapsed plane which exists in no single channel of the cube (B17).
            plane = self.image_viewer.current_plane()
            if plane is None or plane.ndim != 2:
                return
            region = self._cut_region(plane, x0, x1, y0, y1)
            if region is None:
                return
            if calc_method == "Average":
                cut = np.nanmean(region, axis=1) # collapse Y
            elif calc_method == "Median":
                cut = np.nanmedian(region, axis=1)
            else:
                cut = np.nansum(region, axis=1)

            self.top_axis.wavelengths = None
            self.plot_widget.hideAxis('top')
            self.current_wavelengths = None
            self.current_wavelength_unit = ""
                
            x_axis = np.arange(x0, x1)
            self.plot_widget.setLabel('bottom', "X Pixel")
            unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
            self.plot_widget.setLabel('left', f"Intensity ({unit})")
            self.plot_data.setData(x_axis, cut * self.image_viewer.data_multiplier)
            self.plot_bg.setData([], [])
            self.plot_sub.setData([], [])
            
        elif plot_type == "Vertical Cut":
            plane = self.image_viewer.current_plane()
            if plane is None or plane.ndim != 2:
                return
            region = self._cut_region(plane, x0, x1, y0, y1)
            if region is None:
                return
            if calc_method == "Average":
                cut = np.nanmean(region, axis=0) # collapse X
            elif calc_method == "Median":
                cut = np.nanmedian(region, axis=0)
            else:
                cut = np.nansum(region, axis=0)
                
            self.top_axis.wavelengths = None
            self.plot_widget.hideAxis('top')
            self.current_wavelengths = None
            self.current_wavelength_unit = ""
            x_axis = np.arange(y0, y1)
            self.plot_widget.setLabel('bottom', "Y Pixel")
            unit = "DN" if self.image_viewer and getattr(self.image_viewer, 'disp_as_dn', False) else "DN/s"
            self.plot_widget.setLabel('left', f"Intensity ({unit})")
            self.plot_data.setData(x_axis, cut * self.image_viewer.data_multiplier)
            # `plot_bg` too: this branch cleared only the subtracted curve, so a background
            # measured for a depth plot stayed drawn over a cut it had no part in.
            self.plot_bg.setData([], [])
            self.plot_sub.setData([], [])

        self.sync_legend()
        self.update_line_overlays()
