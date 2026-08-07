import os
import sys
from pathlib import Path
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout,
                               QMenu, QFileDialog, QInputDialog,
                               QMessageBox, QApplication)
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from pyql3.core.fits_reader import FitsReader
from pyql3.gui.dialogs.header_editor import HeaderEditorDialog
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.base_tool import as_center
from pyql3.gui.window_manager import get_window_manager
from pyql3.services.poller import DirectoryPoller, watcher_of
from pyql3.gui.dialogs.polling import PollingDialog
from pyql3.services.config import get_config
from PySide6.QtCore import QEvent, Qt, QTimer
import pyql3
from pyql3 import get_resource_path

class MainWindow(QMainWindow):
    """One window, one FITS file, one independent set of tools.

    Several of these are open at once (**File -> New Window**). Each owns its own
    `FitsReader`, `ImageViewer`, tool dialogs and directory poller, so nothing here is
    shared between windows except the settings file -- see `get_config` for why that
    one has to be a single object -- and the window list that decides where a file
    opened from Finder or a shell lands (`pyql3.gui.window_manager`).
    """

    #: Cached tool dialogs, kept as attributes so a second Plot -> Depth Plot reuses
    #: the open one. Listed once here because two things walk them: the display-unit
    #: refresh, and the teardown in `closeEvent`. A new tool must be added to this
    #: tuple or it will neither follow a DN/s <-> Total DN change nor close with its
    #: window.
    TOOL_DIALOG_ATTRS = (
        '_depth_plot_dialog', '_hcut_dialog', '_vcut_dialog', '_dcut_dialog',
        '_strehl_dialog', '_stats_dialog', '_phot_dialog', '_gauss_dialog',
        '_plot_catalog_dialog', '_surf_dialog', '_cont_dialog', '_rotate_dialog',
        '_arith_dialog', '_region_list_dialog',
    )

    def __init__(self):
        super().__init__()
        self.setWindowTitle("QuickLook 3")

        # Set application icon
        icon_path = get_resource_path("pyql3/icon.png")
        self.setWindowIcon(QIcon(icon_path))

        self.resize(600, 850)

        self.fits_reader = FitsReader()
        self.config = get_config()

        # Registered before anything can fail below, so a half-built window is still
        # removed from the list when it is closed.
        get_window_manager().register(self)

        # Setup Poller
        self.poller = DirectoryPoller(self)
        self.poller.file_detected.connect(self.on_file_detected)
        self.poller.batch_coalesced.connect(self.on_batch_coalesced)

        saved_poll_dir = self.config.get("polling_dir")
        if saved_poll_dir:
            self.poller.watch_path = saved_poll_dir
        saved_interval = self.config.get("polling_interval")
        if saved_interval:
            self.poller.interval = saved_interval
        
        # Set up central widget
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Use the new high-performance pyqtgraph ImageViewer
        self.image_viewer = ImageViewer()
        self.main_layout.addWidget(self.image_viewer)
        
        # Connect extension changes
        self.image_viewer.combo_ext.currentIndexChanged.connect(self.on_extension_changed)
        
        # Connect viewer context menu requests
        self.image_viewer.request_depth_plot.connect(self.open_depth_plot)
        self.image_viewer.request_gaussian_fit.connect(self.open_gaussian_fit)
        self.image_viewer.request_new_region.connect(self.spawn_region_at)

        # The region layer draws no dialogs itself; it asks, and this answers.
        self.image_viewer.region_layer.region_activated.connect(self.open_region_properties)
        self.image_viewer.region_layer.region_menu_requested.connect(self.show_region_menu)
        self.image_viewer.region_layer.render_mode_changed.connect(self.on_region_render_mode)
        self.image_viewer.region_layer.labels_suppressed.connect(self.on_region_labels_suppressed)
        #: Open property dialogs, keyed by id() of the region, so double-clicking the same
        #: region twice raises the dialog it already has rather than stacking another.
        self._region_property_dialogs = {}
        #: Built on demand by `show_region_toolbar`; None until then.
        self.region_toolbar = None
        
        self.create_menus()
        self.restore_region_toolbar()
        self.restore_region_labels()

    def create_menus(self):
        menubar = self.menuBar()
        
        # File Menu
        self.file_menu = menubar.addMenu("File")
        
        new_window_action = self.file_menu.addAction("New Window")
        new_window_action.setShortcut(QKeySequence.StandardKey.New)
        new_window_action.triggered.connect(lambda checked=False: self.new_window())

        open_action = self.file_menu.addAction("Open...")
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_file)

        open_new_action = self.file_menu.addAction("Open in New Window...")
        open_new_action.triggered.connect(self.open_file_in_new_window)

        self.recent_menu = self.file_menu.addMenu("Recent Files")
        self.update_recent_files_menu()
        
        save_action = self.file_menu.addAction("Save FITS As...")
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_file_as)
        
        header_action = self.file_menu.addAction("Edit FITS Header")
        header_action.triggered.connect(self.edit_header)
        
        arith_action = self.file_menu.addAction("Arithmetic...")
        arith_action.triggered.connect(self.open_arithmetic_tool)
        
        polling_action = self.file_menu.addAction("Polling...")
        polling_action.triggered.connect(self.open_polling_config)
        
        self.file_menu.addSeparator()

        close_action = self.file_menu.addAction("Close Window")
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close)

        exit_action = self.file_menu.addAction("Exit")
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.setMenuRole(QAction.MenuRole.QuitRole)
        exit_action.triggered.connect(self.close)

        # Display Menu
        self.display_menu = menubar.addMenu("Display")
        
        redisplay_action = self.display_menu.addAction("Redisplay image")
        redisplay_action.triggered.connect(self.redisplay_image)
        
        rotate_action = self.display_menu.addAction("Rotate Image...")
        rotate_action.triggered.connect(self.open_rotate)
        
        self.display_menu.addSeparator()
        
        self.scaling_menu = self.display_menu.addMenu("Scaling")
        
        self.scale_action_group = QActionGroup(self)
        self.scale_actions = {}
        for scale_opt in ["Linear", "Negative", "HistEq", "Logarithmic", "Sqrt", "AsinH"]:
            act = self.scaling_menu.addAction(scale_opt)
            act.setCheckable(True)
            self.scale_action_group.addAction(act)
            self.scale_actions[scale_opt] = act
            act.triggered.connect(lambda checked, s=scale_opt: self.set_scaling(s))
            if scale_opt == "Linear":
                act.setChecked(True)
                
        self.display_menu.addSeparator()
        
        self.colormap_menu = self.display_menu.addMenu("Colormap")
        self.cmap_action_group = QActionGroup(self)
        self.cmap_actions = {}
        
        colormap_groups = [
            ("Perceptually Uniform", ["viridis", "plasma", "inferno", "magma", "cividis"]),
            ("Sequential", ["gray", "Blues", "YlOrBr", "hot"]),
            ("Scientific", ["cmc.oslo", "cmc.grayC", "cmc.devon", "cmc.lapaz", "cmc.vik", "cmc.roma"]),
            ("Diverging", ["RdBu", "coolwarm", "bwr", "seismic", "Spectral"])
        ]
        
        for i, (_group_name, cmaps) in enumerate(colormap_groups):
            if i > 0:
                self.colormap_menu.addSeparator()
            for cmap in cmaps:
                act = self.colormap_menu.addAction(cmap)
                act.setCheckable(True)
                self.cmap_action_group.addAction(act)
                self.cmap_actions[cmap] = act
                act.triggered.connect(lambda checked, c=cmap: self.image_viewer.set_colormap(cmap_name=c))
                if cmap == "cmc.oslo":
                    act.setChecked(True)
                    
        invert_cmap_action = self.display_menu.addAction("Invert Colormap")
        invert_cmap_action.setCheckable(True)
        invert_cmap_action.triggered.connect(lambda checked: self.image_viewer.set_colormap(invert=checked))
                
        self.colorbar_action = self.display_menu.addAction("Show Colorbar")
        self.colorbar_action.setCheckable(True)
        self.colorbar_action.triggered.connect(self.image_viewer.toggle_colorbar)
        
        self.display_menu.addSeparator()
        
        self.pa_action = self.display_menu.addAction("Position Angle")
        self.pa_action.setCheckable(True)
        self.pa_action.triggered.connect(self.toggle_pa)
        
        self.units_menu = self.display_menu.addMenu("Data Units")
        self.unit_action_group = QActionGroup(self)
        
        self.action_dn_s = self.units_menu.addAction("As DN/s")
        self.action_dn_s.setCheckable(True)
        self.action_dn_s.setChecked(True)
        self.unit_action_group.addAction(self.action_dn_s)
        self.action_dn_s.triggered.connect(lambda: self.set_display_unit(False))
        
        self.action_tot_dn = self.units_menu.addAction("As Total DN")
        self.action_tot_dn.setCheckable(True)
        self.unit_action_group.addAction(self.action_tot_dn)
        self.action_tot_dn.triggered.connect(lambda: self.set_display_unit(True))
        
        # Sync scaling changes from viewer combo
        self.image_viewer.combo_scale.currentIndexChanged.connect(self.sync_scaling_from_viewer)

        # Plot Menu
        self.plot_menu = menubar.addMenu("Plot")
        
        depth_plot_action = self.plot_menu.addAction("Depth Plot")
        # Drop QAction.triggered's `checked` flag: no centre is implied from the menu
        depth_plot_action.triggered.connect(lambda checked=False: self.open_depth_plot())
        
        hcut_action = self.plot_menu.addAction("Horizontal Cut")
        hcut_action.triggered.connect(self.open_horizontal_cut)
        
        vcut_action = self.plot_menu.addAction("Vertical Cut")
        vcut_action.triggered.connect(self.open_vertical_cut)
        
        dcut_action = self.plot_menu.addAction("Diagonal Cut")
        dcut_action.triggered.connect(self.open_diagonal_cut)
        
        surf_action = self.plot_menu.addAction("Surface")
        surf_action.triggered.connect(self.open_surface_plot)
        
        cont_action = self.plot_menu.addAction("Contour")
        cont_action.triggered.connect(self.open_contour_plot)
        
        plot_cat_action = self.plot_menu.addAction("Plot Catalog")
        plot_cat_action.triggered.connect(self.open_plot_catalog)
        
        # Analysis Menu
        self.analysis_menu = menubar.addMenu("Analysis")
        stats_action = self.analysis_menu.addAction("Statistics")
        stats_action.triggered.connect(self.open_statistics)
        phot_action = self.analysis_menu.addAction("Photometry")
        phot_action.triggered.connect(self.open_photometry)
        gauss_action = self.analysis_menu.addAction("Gaussian Fit")
        gauss_action.triggered.connect(lambda checked=False: self.open_gaussian_fit())
        
        # Strehl Ratio Tool
        action_strehl = QAction("Strehl Ratio", self)
        action_strehl.triggered.connect(self.open_strehl_tool)
        self.analysis_menu.addAction(action_strehl)
        
        # Removed Math Menu as Arithmetic was moved to File Menu
        
        # Region Menu
        self.region_menu = menubar.addMenu("Region")

        for label, kind in (("New Circle", "circle"), ("New Box", "box"),
                            ("New Arrow", "arrow"), ("New Text...", "text")):
            act = self.region_menu.addAction(label)
            act.triggered.connect(lambda checked=False, k=kind: self.start_drawing_region(k))

        self.region_menu.addSeparator()

        self.region_toolbar_action = self.region_menu.addAction("Region Toolbar")
        self.region_toolbar_action.setCheckable(True)
        self.region_toolbar_action.setToolTip(
            "Show a small vertical bar of region tools beside the image")
        self.region_toolbar_action.toggled.connect(self.show_region_toolbar)

        self.region_labels_action = self.region_menu.addAction("Show Region Labels")
        self.region_labels_action.setCheckable(True)
        self.region_labels_action.setChecked(True)
        self.region_labels_action.setToolTip(
            "Draw each region's text beside it. Turn off for a crowded field.")
        self.region_labels_action.toggled.connect(self.show_region_labels)

        region_list_action = self.region_menu.addAction("Region List...")
        region_list_action.triggered.connect(self.open_region_list)

        send_to_catalog_action = self.region_menu.addAction("Send Regions to Plot Catalog...")
        send_to_catalog_action.setToolTip(
            "Copy the regions into the Plot Catalog tool, which can search and list them")
        send_to_catalog_action.triggered.connect(self.send_regions_to_catalog)

        self.region_menu.addSeparator()

        load_regions_action = self.region_menu.addAction("Load Regions...")
        load_regions_action.triggered.connect(self.load_regions)

        save_regions_action = self.region_menu.addAction("Save Regions As...")
        save_regions_action.triggered.connect(self.save_regions_as)

        export_ds9_action = self.region_menu.addAction("Export ds9 Regions...")
        export_ds9_action.triggered.connect(self.export_ds9_regions)

        self.region_menu.addSeparator()

        delete_regions_action = self.region_menu.addAction("Delete All Regions")
        delete_regions_action.triggered.connect(self.delete_all_regions)

        # Window Menu
        self.window_menu = menubar.addMenu("Window")
        self.window_menu.aboutToShow.connect(self.update_window_menu)
        self.update_window_menu()

        # Help Menu
        self.help_menu = menubar.addMenu("Help")

        # Windows has no equivalent single-directory-on-PATH convention, so the action is
        # simply absent there rather than present and always failing.
        if not sys.platform.startswith('win'):
            install_cli_action = self.help_menu.addAction("Install 'quicklook3' Command Line Tool...")
            install_cli_action.triggered.connect(self.install_cli_tool)
            self.help_menu.addSeparator()

        about_action = self.help_menu.addAction("About QuickLook 3")
        about_action.setMenuRole(QAction.MenuRole.AboutRole)
        about_action.triggered.connect(self.show_about)

    @staticmethod
    def _is_listable_window(widget, exclude=()):
        """True for a widget the Window menu should offer to raise."""
        if not isinstance(widget, QWidget) or widget in exclude:
            return False
        if not widget.isWindow() or not widget.isVisible():
            return False
        if isinstance(widget, QMenu) or widget.inherits("QMenu"):
            return False
        return bool(widget.windowTitle())

    def own_tool_windows(self):
        """This window's own visible tool dialogs, in the order Qt parented them.

        Tools are constructed with the main window as their parent, so ownership is
        exactly Qt's child relationship. Keeping the split per window is what lets the
        menu say which cube a Depth Plot belongs to -- with several windows open, one
        flat list of nine identically-titled dialogs is unusable.
        """
        return [w for w in self.findChildren(QWidget) if self._is_listable_window(w, exclude=(self,))]

    @staticmethod
    def orphan_tool_windows():
        """Visible windows owned by no main window, so nothing drops out of the menu.

        Nothing is expected here; it is a safety net for a dialog created without a
        parent, which would otherwise be unreachable once it fell behind another window.
        """
        app = QApplication.instance()
        if not app:
            return []
        mains = get_window_manager().windows()
        owned = set()
        for main in mains:
            owned.update(main.findChildren(QWidget))
        return [w for w in app.topLevelWidgets()
                if MainWindow._is_listable_window(w, exclude=tuple(mains)) and w not in owned]

    def get_open_tool_windows(self):
        """Visible tool windows relevant to this main window (its own, plus orphans)."""
        return self.own_tool_windows() + self.orphan_tool_windows()

    def bring_window_to_front(self, window):
        """Brings the specified window or dialog to front and focuses it."""
        if window:
            if window.isMinimized():
                window.showNormal()
            window.show()
            window.raise_()
            window.activateWindow()

    def bring_all_to_front(self):
        """Brings every main window of this application, and their dialogs, to front."""
        for main in get_window_manager().windows():
            if main is not self:
                self.bring_window_to_front(main)
                for w in main.own_tool_windows():
                    self.bring_window_to_front(w)
        for w in self.orphan_tool_windows():
            self.bring_window_to_front(w)
        # This window last, so the menu the user just used ends up on top.
        self.bring_window_to_front(self)
        for w in self.own_tool_windows():
            self.bring_window_to_front(w)

    def _add_raise_action(self, menu, window, title):
        act = menu.addAction(title)
        act.setCheckable(True)
        if window.isActiveWindow():
            act.setChecked(True)
        act.triggered.connect(lambda checked=False, target=window: self.bring_window_to_front(target))
        return act

    @staticmethod
    def _disambiguated_titles(windows):
        """Menu labels for `windows`, numbering repeats of the same title.

        Two windows showing the same cube, or two Depth Plots, are otherwise
        indistinguishable in the menu.
        """
        counts = {}
        labels = []
        for w in windows:
            base = w.windowTitle() or type(w).__name__
            counts[base] = counts.get(base, 0) + 1
            labels.append(base if counts[base] == 1 else f"{base} #{counts[base]}")
        return labels

    def update_window_menu(self):
        """Populate the Window menu with every open window of this application.

        With one main window this is the flat list it has always been. With several, each
        window's tools are grouped under it, so it is clear which cube a given Depth Plot
        or Statistics window is reading.
        """
        if not hasattr(self, 'window_menu'):
            return

        self.window_menu.clear()

        bring_all_act = self.window_menu.addAction("Bring All to Front")
        bring_all_act.triggered.connect(self.bring_all_to_front)

        self.window_menu.addSeparator()

        mains = get_window_manager().windows()
        if self not in mains:
            mains = mains + [self]
        main_labels = self._disambiguated_titles(mains)

        if len(mains) == 1:
            self._add_raise_action(self.window_menu, self, main_labels[0] or "QuickLook 3")
            tool_windows = self.get_open_tool_windows()
            if tool_windows:
                self.window_menu.addSeparator()
                for w, label in zip(tool_windows, self._disambiguated_titles(tool_windows), strict=True):
                    self._add_raise_action(self.window_menu, w, label)
            return

        for main, label in zip(mains, main_labels, strict=True):
            tools = main.own_tool_windows()
            if not tools:
                self._add_raise_action(self.window_menu, main, label)
                continue

            submenu = self.window_menu.addMenu(label)
            self._add_raise_action(submenu, main, "Bring to Front")
            submenu.addSeparator()
            for w, tool_label in zip(tools, self._disambiguated_titles(tools), strict=True):
                self._add_raise_action(submenu, w, tool_label)

        orphans = self.orphan_tool_windows()
        if orphans:
            self.window_menu.addSeparator()
            for w, label in zip(orphans, self._disambiguated_titles(orphans), strict=True):
                self._add_raise_action(self.window_menu, w, label)

    def install_cli_tool(self):
        """Write a `quicklook3` launcher on PATH, so the app can be started like ds9.

        The running application knows its own location, which is why this belongs in the
        GUI: a user who installed from the .dmg never has to work out where the bundle
        ended up or which directory is on their PATH.

        Nothing is written until the user has seen the exact path and agreed to it. A menu
        item that silently drops an executable somewhere on PATH is not something anyone
        should have to discover after the fact.
        """
        from pyql3.services.cli_install import CliInstallError, describe_plan, install, plan

        try:
            proposed = plan()
        except CliInstallError as exc:
            QMessageBox.warning(self, "Install Command Line Tool", str(exc))
            return

        if not self.confirm_cli_install(proposed):
            return

        try:
            path = install(plan_=proposed)
        except CliInstallError as exc:
            QMessageBox.warning(self, "Install Command Line Tool", str(exc))
            return

        QMessageBox.information(
            self, "Install Command Line Tool",
            f"Installed the '{path.name}' command.\n\n"
            + "\n".join(describe_plan(proposed, done=True)))

    def confirm_cli_install(self, proposed):
        """Show what will be written and return True only if the user accepts.

        Split out so the decision can be driven in tests, and so this method stays purely
        about presenting the plan.
        """
        from pyql3.services.cli_install import describe_plan

        box = QMessageBox(self)
        box.setWindowTitle("Install Command Line Tool")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"Install the '{proposed.name}' command line tool?")
        # Plain text, not rich: paths and shell lines must not be re-interpreted as markup
        box.setInformativeText("\n".join(describe_plan(proposed)))
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Ok).setText("Install")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)

        return box.exec() == QMessageBox.StandardButton.Ok

    def show_about(self):
        QMessageBox.about(
            self,
            "About QuickLook 3",
            f"<h3>QuickLook 3</h3>"
            f"<p>Version: {pyql3.__version__}</p>"
            f"<p>A modern Python/Qt-based application for viewing integral field spectroscopy data.</p>"
            f"<p>Developed by Tuan Do (UCLA).<br>"
            f"Based on QuickLook 2 (ql2) for IDL.</p>"
            f"<p><a href='https://github.com/astrodatalab/pyql3'>https://github.com/astrodatalab/pyql3</a></p>"
        )

    FITS_FILE_FILTER = "FITS Files (*.fits *.fit *.fits.gz);;All Files (*)"

    def open_file(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Open FITS File", "", self.FITS_FILE_FILTER)
        if filepath:
            self.load_fits(filepath)

    def open_file_in_new_window(self):
        """Load a file into a second window, leaving this one as it is."""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open FITS File in New Window", "", self.FITS_FILE_FILTER)
        if filepath:
            self.new_window(filepath)

    def new_window(self, filepath=None):
        """Open another independent main window, optionally with a file already loaded.

        The new window gets its own reader, viewer, tools and poller; only the settings
        file is shared. Returns the window so callers (and tests) can drive it.
        """
        window = get_window_manager().new_window(near=self)
        if filepath:
            window.load_fits(filepath)
        return window

    def changeEvent(self, event):
        """Track which window was used last, for files that arrive without one.

        A file double-clicked in Finder, or a `quicklook3 cube.fits` run while the
        application is open, has no window attached to it and goes to the most recently
        used one.
        """
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            get_window_manager().touch(self)
        super().changeEvent(event)

    def closeEvent(self, event):
        """Release everything this window owns before it goes away.

        Each of these leaks per closed window if skipped: the poller keeps a
        `PollingObserver` thread scanning a directory nobody is watching any more; tool
        dialogs are top-level windows, so they stay on screen after their window is
        gone (and, being visible windows, keep the application alive once the last main
        window closes); and the open FITS handle keeps the file un-replaceable on
        Windows, where an open file cannot be unlinked or overwritten.

        The viewer's arrays go too. Qt does not destroy a closed window — see
        `ImageViewer.release_data()` — so without this the whole cube stays in memory for as long
        as the application runs, three times over, for every window ever closed.
        """
        self._closed = True
        self.poller.stop_polling()
        self.close_tool_dialogs()
        self.fits_reader.close()
        self.image_viewer.release_data()
        get_window_manager().unregister(self)
        super().closeEvent(event)

    def close_tool_dialogs(self):
        """Close this window's tool dialogs, letting each drop its ROI from the viewer."""
        for attr in self.TOOL_DIALOG_ATTRS:
            dialog = getattr(self, attr, None)
            if dialog is None:
                continue
            try:
                dialog.close()
            except RuntimeError:
                # Already destroyed by Qt; nothing left to tidy.
                pass

    def load_fits(self, filepath, ext=None, force=False, show_errors=True):
        """Load and display a FITS file.

        Returns True on success. `show_errors=False` suppresses the error dialog and
        reports failure through the return value instead, which the polling auto-load
        uses so it can retry a file that is still arriving rather than interrupting
        the observer with a dialog on the first attempt.
        """
        try:
            self.fits_reader.load(filepath, ext=ext, force=force)
            data = self.fits_reader.get_data()
            if data is not None:
                header = self.fits_reader.get_header()
                self.image_viewer.set_data(data, header=header)
                filename = os.path.basename(filepath)
                self.setWindowTitle(f"QuickLook 3 - {filename}")
                
                # Update extension combobox
                extensions = self.fits_reader.get_image_extensions()
                current_ext = self.fits_reader.current_ext
                
                self.image_viewer.combo_ext.blockSignals(True)
                self.image_viewer.combo_ext.clear()
                for idx, name in extensions:
                    self.image_viewer.combo_ext.addItem(f"{idx}: {name}", userData=idx)
                
                combo_idx = self.image_viewer.combo_ext.findData(current_ext)
                if combo_idx >= 0:
                    self.image_viewer.combo_ext.setCurrentIndex(combo_idx)
                self.image_viewer.combo_ext.blockSignals(False)

                # Add to recent files and update menu
                self.config.add_recent_file(filepath)
                self.update_recent_files_menu()
                return True
            else:
                if show_errors:
                    QMessageBox.warning(self, "Warning", "No valid data found in FITS file.")
                return False
        except Exception as e:
            if show_errors:
                QMessageBox.critical(self, "Error", f"Failed to open FITS file:\n{str(e)}")
            self._last_load_error = str(e)
            return False

    def update_recent_files_menu(self):
        if not hasattr(self, 'recent_menu'):
            return
        self.recent_menu.clear()
        recent_files = self.config.get_recent_files()
        
        if not recent_files:
            no_recent = self.recent_menu.addAction("No Recent Files")
            no_recent.setEnabled(False)
        else:
            basenames = [os.path.basename(p) for p in recent_files]
            for idx, path in enumerate(recent_files):
                base = os.path.basename(path)
                if basenames.count(base) > 1:
                    parent_dir = os.path.basename(os.path.dirname(path))
                    action_text = f"{idx + 1}. {base} ({parent_dir})"
                else:
                    action_text = f"{idx + 1}. {base}"
                    
                act = self.recent_menu.addAction(action_text)
                act.setToolTip(path)
                act.setStatusTip(path)
                act.triggered.connect(lambda checked=False, p=path: self.open_recent_file(p))
            
            self.recent_menu.addSeparator()
            clear_action = self.recent_menu.addAction("Clear Recent Files")
            clear_action.triggered.connect(self.clear_recent_files)

    def open_recent_file(self, filepath):
        if os.path.exists(filepath):
            self.load_fits(filepath)
        else:
            reply = QMessageBox.warning(
                self,
                "File Not Found",
                f"The file no longer exists at:\n{filepath}\n\nRemove from recent files list?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.config.remove_recent_file(filepath)
                self.update_recent_files_menu()

    def clear_recent_files(self):
        self.config.clear_recent_files()
        self.update_recent_files_menu()


    def save_file_as(self):
        data = self.fits_reader.get_data()
        if data is None:
            QMessageBox.warning(self, "Warning", "No data to save.")
            return
            
        header = self.fits_reader.get_header()
        filepath, _ = QFileDialog.getSaveFileName(self, "Save FITS File", "", "FITS Files (*.fits)")
        if filepath:
            if not filepath.endswith('.fits'):
                filepath += '.fits'
            try:
                from astropy.io import fits
                hdu = fits.PrimaryHDU(data=data, header=header)
                hdu.writeto(filepath, overwrite=True)
                QMessageBox.information(self, "Success", f"Saved {filepath}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save FITS file:\n{str(e)}")

    def load_from_memory(self, data, header, title):
        try:
            self.fits_reader.load_from_memory(data, header)
            self.image_viewer.set_data(data, header=header)
            filename = os.path.basename(title)
            self.setWindowTitle(f"QuickLook 3 - {filename}")
            
            # Update extension combobox
            extensions = self.fits_reader.get_image_extensions()
            current_ext = self.fits_reader.current_ext
            
            self.image_viewer.combo_ext.blockSignals(True)
            self.image_viewer.combo_ext.clear()
            for idx, name in extensions:
                self.image_viewer.combo_ext.addItem(f"{idx}: {name}", userData=idx)
            
            combo_idx = self.image_viewer.combo_ext.findData(current_ext)
            if combo_idx >= 0:
                self.image_viewer.combo_ext.setCurrentIndex(combo_idx)
            self.image_viewer.combo_ext.blockSignals(False)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load data from memory:\n{str(e)}")

    def on_extension_changed(self, index):
        if index < 0:
            return
        ext_idx = self.image_viewer.combo_ext.itemData(index)
        if ext_idx is not None and self.fits_reader.filepath:
            self.load_fits(self.fits_reader.filepath, ext=ext_idx)

    def redisplay_image(self):
        # force=True so this is always a genuine re-read from disk, even for a rewrite that
        # somehow preserved size and timestamp
        if self.fits_reader.filepath:
            ext_idx = self.fits_reader.current_ext
            self.load_fits(self.fits_reader.filepath, ext=ext_idx, force=True)
            
    def set_scaling(self, scale_opt):
        # Find index in combo box
        idx = self.image_viewer.combo_scale.findText(scale_opt)
        if idx >= 0:
            self.image_viewer.combo_scale.blockSignals(True)
            self.image_viewer.combo_scale.setCurrentIndex(idx)
            self.image_viewer.combo_scale.blockSignals(False)
            self.image_viewer.update_image_display()
            
    def sync_scaling_from_viewer(self):
        scale_opt = self.image_viewer.combo_scale.currentText()
        if scale_opt in self.scale_actions:
            self.scale_actions[scale_opt].setChecked(True)
            
    def toggle_pa(self, checked):
        self.image_viewer.toggle_position_angle(checked)
        
    def set_display_unit(self, as_total_dn):
        if self.image_viewer.disp_as_dn != as_total_dn:
            self.image_viewer.disp_as_dn = as_total_dn
            self.image_viewer.update_colorbar_label()
            
            if self.image_viewer.transposed_data is not None:
                self.image_viewer.refresh_display()
                
            self.update_tools_for_unit()

    def update_tools_for_unit(self):
        """Update any open tool dialogs when the display unit changes."""
        for attr in self.TOOL_DIALOG_ATTRS:
            if hasattr(self, attr):
                dialog = getattr(self, attr)
                if dialog and dialog.isVisible():
                    if hasattr(dialog, 'update_plot'):
                        dialog.update_plot()
                    if hasattr(dialog, 'update_stats'):
                        dialog.update_stats()

    def edit_header(self):
        if self.fits_reader.get_header() is None:
            QMessageBox.warning(self, "Warning", "Please load a FITS file first.")
            return
            
        dialog = HeaderEditorDialog(self.fits_reader, self)
        dialog.exec()

    def open_depth_plot(self, initial_center=None):
        initial_center = as_center(initial_center)
        if self.image_viewer is None or getattr(self.image_viewer, 'transposed_data', None) is None:
            return
        if self.image_viewer.transposed_data.ndim != 3:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Depth Plot", "Depth Plot is only available for 3D data cubes.")
            return

        from pyql3.gui.tools.depth_plot import DepthPlotDialog
        if not hasattr(self, '_depth_plot_dialog') or not self._depth_plot_dialog.isVisible():
            # The constructor already honours initial_center
            self._depth_plot_dialog = DepthPlotDialog(self, self.image_viewer, initial_center=initial_center)
        elif initial_center is not None:
            # Re-target an already-open dialog
            self._depth_plot_dialog.set_center(initial_center)
        self._depth_plot_dialog.show()
        self._depth_plot_dialog.raise_()

    def open_horizontal_cut(self):
        from pyql3.gui.tools.cuts import CutPlotDialog
        self._hcut_dialog = CutPlotDialog('horizontal', self, self.image_viewer)
        self._hcut_dialog.show()
        
    def open_vertical_cut(self):
        from pyql3.gui.tools.cuts import CutPlotDialog
        self._vcut_dialog = CutPlotDialog('vertical', self, self.image_viewer)
        self._vcut_dialog.show()
        
    def open_diagonal_cut(self):
        from pyql3.gui.tools.cuts import CutPlotDialog
        self._dcut_dialog = CutPlotDialog('diagonal', self, self.image_viewer)
        self._dcut_dialog.show()
        
    def open_surface_plot(self):
        from pyql3.gui.tools.advanced_plots import SurfacePlotDialog
        self._surf_dialog = SurfacePlotDialog(self, self.image_viewer)
        try:
            self._surf_dialog.show()
        except Exception as e:
            print(f"Warning: 3D OpenGL Surface Plot unavailable in headless/virtualized GPU environment: {e}")
        
    def open_contour_plot(self):
        from pyql3.gui.tools.advanced_plots import ContourDialog
        self._cont_dialog = ContourDialog(self, self.image_viewer)
        self._cont_dialog.show()
        
    def open_plot_catalog(self):
        from pyql3.gui.tools.plot_catalog import PlotCatalogDialog
        if not hasattr(self, '_plot_catalog_dialog') or not self._plot_catalog_dialog.isVisible():
            self._plot_catalog_dialog = PlotCatalogDialog(self, self.image_viewer)
        self._plot_catalog_dialog.show()
        self._plot_catalog_dialog.raise_()
        
    def open_rotate(self):
        from pyql3.gui.tools.rotate import RotateDialog
        if not hasattr(self, '_rotate_dialog') or not self._rotate_dialog.isVisible():
            self._rotate_dialog = RotateDialog(self, self.image_viewer)
        self._rotate_dialog.show()
        self._rotate_dialog.raise_()
        
    def open_statistics(self):
        from pyql3.gui.tools.statistics import StatisticsDialog
        if not hasattr(self, '_stats_dialog') or not self._stats_dialog.isVisible():
            self._stats_dialog = StatisticsDialog(self, self.image_viewer)
        self._stats_dialog.show()
        self._stats_dialog.raise_()

    def open_photometry(self):
        from pyql3.gui.tools.photometry import PhotometryDialog
        if not hasattr(self, '_phot_dialog') or not self._phot_dialog.isVisible():
            self._phot_dialog = PhotometryDialog(self, self.image_viewer)
        self._phot_dialog.show()
        self._phot_dialog.raise_()

    def open_gaussian_fit(self, initial_center=None):
        initial_center = as_center(initial_center)
        from pyql3.gui.tools.fitting import GaussianFitDialog
        if not hasattr(self, '_gauss_dialog') or not self._gauss_dialog.isVisible():
            self._gauss_dialog = GaussianFitDialog(self, self.image_viewer, initial_center=initial_center)
        self._gauss_dialog.show()
        self._gauss_dialog.raise_()
        if initial_center is not None and hasattr(self._gauss_dialog, 'set_center'):
            self._gauss_dialog.set_center(initial_center)


    # ----------------------------------------------------------------- regions

    @property
    def region_layer(self):
        return getattr(self.image_viewer, 'region_layer', None)

    #: Settings key for whether the region toolbar is shown. Remembered because it is a standing
    #: preference about the window's shape, not a per-file choice.
    REGION_TOOLBAR_SETTING = "region_toolbar"

    def show_region_toolbar(self, visible):
        """Show or hide the vertical bar of region tools, and remember the choice.

        Built the first time it is asked for: a window that never shows it should not pay for four
        painted icons.
        """
        from pyql3.gui.region_toolbar import RegionToolBar

        if visible and getattr(self, 'region_toolbar', None) is None:
            self.region_toolbar = RegionToolBar(self)
            self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self.region_toolbar)

        if getattr(self, 'region_toolbar', None) is not None:
            self.region_toolbar.setVisible(bool(visible))

        if self.region_toolbar_action.isChecked() != bool(visible):
            self.region_toolbar_action.setChecked(bool(visible))
        self.config.set(self.REGION_TOOLBAR_SETTING, bool(visible))

    #: Settings key for whether region labels are drawn.
    REGION_LABELS_SETTING = "region_labels"

    def show_region_labels(self, visible):
        """Draw or hide every region's text, and remember the choice.

        The catalogue tool offers the same switch for source names: with a few thousand labelled
        regions, whether the text helps or gets in the way is the user's call to make.
        """
        if self.region_layer is not None:
            self.region_layer.set_labels_visible(visible)
        if self.region_labels_action.isChecked() != bool(visible):
            self.region_labels_action.setChecked(bool(visible))
        self.config.set(self.REGION_LABELS_SETTING, bool(visible))

    def restore_region_labels(self):
        """Apply the remembered label preference. Labels are on unless turned off."""
        if not self.config.get(self.REGION_LABELS_SETTING, True):
            self.region_labels_action.setChecked(False)

    def restore_region_toolbar(self):
        """Apply the remembered toolbar preference. Called once the window is built."""
        if self.config.get(self.REGION_TOOLBAR_SETTING, False):
            # setChecked drives show_region_toolbar through the toggled signal.
            self.region_toolbar_action.setChecked(True)

    def open_region_list(self):
        from pyql3.gui.tools.region_list import RegionListDialog
        if not hasattr(self, '_region_list_dialog') or not self._region_list_dialog.isVisible():
            self._region_list_dialog = RegionListDialog(self, self.image_viewer)
        self._region_list_dialog.show()
        self._region_list_dialog.raise_()

    def open_region_properties(self, region):
        """Open the ds9-style editor for one region, or raise the one already open for it.

        Keyed by `id(region)` deliberately: model regions are dataclasses compared by value, so
        two identical circles are `==` and a dict keyed by the region itself would confuse them
        (and could not be built at all, since they are unhashable).
        """
        from pyql3.gui.dialogs.region_properties import RegionPropertiesDialog

        if self.region_layer is None:
            return

        key = id(region)
        existing = self._region_property_dialogs.get(key)
        if existing is not None:
            try:
                if existing.isVisible():
                    existing.raise_()
                    existing.activateWindow()
                    return existing
            except RuntimeError:
                pass        # the dialog was destroyed; fall through and make another

        dialog = RegionPropertiesDialog(region, self.region_layer, self)
        self._region_property_dialogs[key] = dialog
        dialog.finished.connect(lambda _result, k=key: self._region_property_dialogs.pop(k, None))
        dialog.show()
        dialog.raise_()
        return dialog

    def on_region_render_mode(self, bulk, count):
        """Say when regions stop being individually editable, rather than letting it be a mystery.

        Above the layer's limit the whole set is drawn as a few aggregate items — see
        `region_layer.INTERACTIVE_LIMIT` for the measurements behind that — which means no dragging
        and no right-clicking. Silently losing interaction would read as a bug.
        """
        from pyql3.gui.viewers.region_layer import INTERACTIVE_LIMIT

        if bulk:
            self.statusBar().showMessage(
                f"{count:,} regions: drawn as a fixed overlay, since more than "
                f"{INTERACTIVE_LIMIT:,} cannot each be dragged. Editing still works through "
                "Region ➔ Region List.", 12000)
        else:
            self.statusBar().showMessage(
                f"{count:,} regions: individually editable again.", 6000)

    def on_region_labels_suppressed(self, count):
        """Say why the labels went away, rather than leaving it looking like they were lost."""
        from pyql3.gui.viewers.region_layer import LABEL_SAFETY_LIMIT

        if count:
            self.statusBar().showMessage(
                f"{count:,} labels in view — more than the {LABEL_SAFETY_LIMIT:,} that can be "
                "drawn at once, so none are. Zoom in, or turn them off with "
                "Region ➔ Show Region Labels.", 8000)
        else:
            self.statusBar().clearMessage()

    def load_regions_from(self, filepath, announce=True):
        """Load a region file into this window. Returns True if anything was loaded.

        Shared by the menu and the `--regions` command-line flag, so the two cannot diverge. Only
        the menu may put a question to the user: a command-line load takes the frame the file was
        saved with rather than stopping a startup on a dialog.
        """
        from pyql3.core.regions_io import load_regions
        from pyql3.core.regions_model import RegionFormatError

        if self.region_layer is None:
            return False

        try:
            region_list, report = load_regions(
                filepath, wcs=self.image_viewer.wcs,
                axis_indices=self.image_viewer.display_axis_indices(),
                choose_frame=self.choose_region_frame if announce else None)
        except (RegionFormatError, OSError) as exc:
            if announce:
                QMessageBox.warning(self, "Load Regions", str(exc))
            else:
                print(f"pyql3: could not load regions from {filepath}: {exc}", file=sys.stderr)
            return False

        self.region_layer.set_regions(region_list.regions)
        if announce:
            self._report_region_conversion("Load Regions", report,
                                           f"Loaded {len(region_list)} region(s).")
        return True

    def choose_region_frame(self, offer):
        """Ask which of a file's two coordinate frames to place its regions in.

        A region file written with a WCS holds both — pixels of the image it was drawn on, and
        RA/Dec — and on a different pointing they disagree. ds9 puts the same question. It is only
        asked when the answer would move something: `regions_io.placed_on_image` skips the hook
        when the frames agree, which is every file loaded back onto its own image.

        **A test that reaches this must stub it**, as with `confirm_cli_install` — a real modal
        dialog blocks the suite until it times out.
        """
        box = QMessageBox(self)
        box.setWindowTitle("Load Regions")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("This region file gives every region in two coordinate frames.")
        box.setInformativeText(
            f"{offer.summary()} on this image, so the file was drawn on a different pointing.\n\n"
            "Sky coordinates put each region back on the same piece of sky. Image coordinates "
            "put it on the same pixel of this file.")
        sky = box.addButton("Sky (RA/Dec)", QMessageBox.ButtonRole.AcceptRole)
        image = box.addButton("Image (pixels)", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(sky if offer.saved == "sky" else image)
        box.exec()

        return "image" if box.clickedButton() is image else "sky"

    def build_region_menu(self, region):
        """The context menu for a region on the image.

        pyqtgraph's own ROI menu cannot be used: it walks up to the ImageItem these items are
        parented to, whose `getContextMenus()` returns `[None]`, and pyqtgraph raises trying to add
        that to a menu (`BUGS.md` M12). The layer asks for this instead.

        Kept separate from showing it because `QMenu.exec` is modal and blocks until dismissed,
        which makes a menu impossible to inspect otherwise.
        """
        from pyql3.gui.dialogs.region_properties import copy_region_coordinates

        menu = QMenu(self)
        menu.addAction("Properties...").triggered.connect(
            lambda: self.open_region_properties(region))
        menu.addAction("Copy Coordinates").triggered.connect(
            lambda: self.statusBar().showMessage(
                f"Copied: {copy_region_coordinates(region, self.image_viewer)}", 6000))
        menu.addSeparator()
        menu.addAction("Delete").triggered.connect(lambda: self.region_layer.remove(region))
        return menu

    def show_region_menu(self, region, global_position):
        """Pop up `build_region_menu` at a screen position."""
        if self.region_layer is None:
            return None

        menu = self.build_region_menu(region)
        # Held on self: a QMenu with no Python owner is deleted before it can be shown.
        self._region_menu = menu
        if global_position is not None:
            menu.exec(global_position)
        return menu

    def send_regions_to_catalog(self):
        """Copy the regions into the Plot Catalog tool as a source list.

        Worth having because the two overlays are good at different things: regions are drawn and
        edited individually, while the catalogue tool has a table, a search box and row
        highlighting, and stays usable at sizes where regions stop being individually editable.

        A copy, not a link: the catalogue is a snapshot taken now, and editing a region afterwards
        does not change it. Coordinates go across as *FITS Pixels*, which is what the catalogue
        calls the orig coordinates regions are stored in.
        """
        from astropy.table import Table

        from pyql3.core.regions_model import sizes_of

        layer = self.region_layer
        if layer is None or not len(layer):
            QMessageBox.information(self, "Send Regions to Plot Catalog",
                                    "There are no regions to send.")
            return

        regions = layer.regions
        table = Table({
            # `name`, `x` and `y` are the column names the catalogue tool recognises, so it
            # configures its own coordinate type and label column without being told.
            "name": [region.text or f"{region.TYPE} {index + 1}"
                     for index, region in enumerate(regions)],
            "x": [float(region.x) for region in regions],
            "y": [float(region.y) for region in regions],
            "type": [region.TYPE for region in regions],
            "size": [float(sizes_of(region)[0] or 0.0) for region in regions],
        })

        self.open_plot_catalog()
        source = os.path.basename(self.fits_reader.filepath or "") or "regions"
        self._plot_catalog_dialog.set_catalog_table(table, f"{len(regions)} regions from {source}")
        self.statusBar().showMessage(
            f"Sent {len(regions):,} regions to the Plot Catalog tool. They are a copy: editing a "
            "region will not change the catalogue.", 8000)

    def start_drawing_region(self, kind):
        """Enter drawing mode for one shape.

        A text region is clicked into place and *then* asked about, which is the order the work
        happens in: point at the feature, then say what it is called. The layer puts nothing on
        screen itself, so it calls back here for the label once the click has landed.
        """
        if self.region_layer is None or self.image_viewer.transposed_data is None:
            QMessageBox.information(self, "Region", "Load a FITS file before drawing regions.")
            return

        if kind == "text":
            self.region_layer.begin_draw(kind, ask_text=self.ask_region_label)
            self.statusBar().showMessage(
                "Click the image where the label should go.", 6000)
            return

        self.region_layer.begin_draw(kind)
        self.statusBar().showMessage(f"Drag on the image to draw a {kind}.", 6000)

    def spawn_region_at(self, kind, position):
        """Put a default-sized region where the user right-clicked.

        Pointing at a feature and getting a region there is quicker than dragging one out, and the
        size is easy to change afterwards — from the properties dialog, the Region List, or by
        dragging a handle.
        """
        from pyql3.core import coords

        if self.region_layer is None or self.image_viewer.transposed_data is None:
            QMessageBox.information(self, "Region", "Load a FITS file before drawing regions.")
            return None
        if position is None:
            return None

        # The stored position is a display *pixel*; a region wants its centre, half a pixel on.
        item_x = coords.index_to_item(position[0])
        item_y = coords.index_to_item(position[1])

        region = self.region_layer.place(
            kind, item_x, item_y,
            ask_text=self.ask_region_label if kind == "text" else None)
        if region is not None:
            self.statusBar().showMessage(
                f"Added a {kind} here. Drag its handle to resize, or double-click it to edit.",
                6000)
        return region

    def ask_region_label(self, x, y):
        """Ask for a text region's label, having been told where it will go.

        Returns the label, or an empty string to place nothing. Called by the region layer after
        the click, so the position can be shown in the prompt.
        """
        label, accepted = QInputDialog.getText(
            self, "Text Region", f"Label for the region at ({x:.1f}, {y:.1f}):")
        return label.strip() if accepted else ""

    def delete_all_regions(self):
        layer = self.region_layer
        if layer is None or not len(layer):
            return
        count = len(layer)
        reply = QMessageBox.question(
            self, "Delete All Regions",
            f"Delete all {count} region{'s' if count != 1 else ''}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            layer.clear()

    def load_regions(self):
        """Open a region file, in either format, and replace what is on screen."""
        from pyql3.core.regions_io import FILE_FILTERS

        if self.region_layer is None:
            return
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Load Regions", "", ";;".join(FILE_FILTERS))
        if filepath:
            self.load_regions_from(filepath)

    def save_regions_as(self):
        """Save in the native format, or ds9's if the name says so."""
        from pyql3.core.regions_io import FILE_FILTERS, suggested_filename

        layer = self.region_layer
        if layer is None:
            return
        if not len(layer):
            QMessageBox.information(self, "Save Regions", "There are no regions to save.")
            return

        default = suggested_filename(self.fits_reader.filepath, ".yml")
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Save Regions", default, ";;".join(FILE_FILTERS))
        if not filepath:
            return
        if not Path(filepath).suffix:
            filepath += ".yml"

        report = self._write_regions(filepath, layer.regions)
        if report is not None:
            self._report_region_conversion("Save Regions", report,
                                           f"Saved {len(layer)} region(s) to {filepath}.")

    def export_ds9_regions(self):
        """Save as ds9 `.reg` regardless of the name given."""
        from pyql3.core.regions_io import suggested_filename

        layer = self.region_layer
        if layer is None:
            return
        if not len(layer):
            QMessageBox.information(self, "Export ds9 Regions", "There are no regions to export.")
            return

        default = suggested_filename(self.fits_reader.filepath, ".reg")
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Export ds9 Regions", default, "ds9 regions (*.reg);;All files (*)")
        if not filepath:
            return
        if Path(filepath).suffix.lower() != ".reg":
            # The exporter picks its format from the suffix, so this has to be honest.
            filepath = str(Path(filepath).with_suffix(".reg"))

        report = self._write_regions(filepath, layer.regions)
        if report is not None:
            self._report_region_conversion("Export ds9 Regions", report,
                                           f"Exported {len(layer)} region(s) to {filepath}.")

    def _write_regions(self, filepath, regions):
        """Write regions and return the conversion report, or None if the write failed.

        The native format has nothing to report, so `save_regions` returns None for it; an empty
        report stands in, keeping "wrote it, nothing to say" distinct from "did not write it".
        """
        from pyql3.core.ds9_regions import Report
        from pyql3.core.regions_io import save_regions

        try:
            report = save_regions(
                filepath, regions, wcs=self.image_viewer.wcs,
                axis_indices=self.image_viewer.display_axis_indices(),
                written_by=f"QuickLook 3 v{pyql3.__version__}",
                source=os.path.basename(self.fits_reader.filepath or ""))
        except OSError as exc:
            QMessageBox.warning(self, "Save Regions", f"Could not write the file:\n{exc}")
            return None
        return report if report is not None else Report()

    def _report_region_conversion(self, title, report, success):
        """Say what was lost converting to or from ds9, rather than dropping it silently."""
        if report is not None and (report.skipped or report.notes):
            QMessageBox.information(self, title, f"{success}\n\n{report.summary()}")
        else:
            self.statusBar().showMessage(success, 6000)

    def open_arithmetic_tool(self):
        from .tools.arithmetic import ArithmeticDialog
        if not hasattr(self, '_arith_dialog') or not self._arith_dialog.isVisible():
            self._arith_dialog = ArithmeticDialog(self, self.image_viewer)
        self._arith_dialog.show()
        self._arith_dialog.raise_()

    def open_strehl_tool(self):
        from pyql3.gui.tools.strehl import StrehlDialog
        if not hasattr(self, '_strehl_dialog') or not self._strehl_dialog.isVisible():
            self._strehl_dialog = StrehlDialog(self, self.image_viewer)
        self._strehl_dialog.show()
        self._strehl_dialog.raise_()

    def confirm_watch_takeover(self, path):
        """Ask before moving another window's directory watch to this one.

        Only one window watches a given directory (see `pyql3.services.poller`), so this
        is a real change of destination for every frame that arrives, not a duplicate
        watch. Returning True lets `start_polling` take it over.
        """
        other = watcher_of(path)
        if other is None or other is self.poller:
            return True

        owner = other.parent()
        owner_title = owner.windowTitle() if owner is not None and hasattr(owner, 'windowTitle') else ""
        owner_name = f'"{owner_title}"' if owner_title else "Another window"

        reply = QMessageBox.question(
            self,
            "Directory Already Watched",
            f"{owner_name} is already watching:\n{path}\n\n"
            "A directory is watched by one window at a time, so new files appear in a "
            "single place. Move the watch to this window?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return reply == QMessageBox.StandardButton.Yes

    def open_polling_config(self):
        dialog = PollingDialog(self.poller, self, config=self.config,
                               confirm_takeover=self.confirm_watch_takeover)
        if dialog.exec():
            # Save the active polling dir to config if started
            if self.poller.watch_path:
                self.config.set("polling_dir", self.poller.watch_path)
        
    #: Auto-load retry schedule, in seconds. The poller only announces a file whose
    #: size has held steady, but on NFS the client's attribute cache can report a
    #: stale size for a file still growing on the server, so a settled-looking file
    #: can still fail to parse. A failed parse therefore means "not ready yet", not
    #: "corrupt" — we back off and try again before bothering the observer.
    AUTO_LOAD_RETRY_DELAYS = (0.0, 1.0, 2.0, 4.0, 8.0)

    def on_file_detected(self, filepath):
        self._attempt_auto_load(filepath, 0)

    def _attempt_auto_load(self, filepath, attempt):
        # A retry is scheduled on a timer, so it can outlive the window it belongs to.
        # Loading into a closed window -- or worse, warning from one -- is not wanted.
        if getattr(self, '_closed', False):
            return

        if self.load_fits(filepath, show_errors=False):
            return

        next_attempt = attempt + 1
        if next_attempt < len(self.AUTO_LOAD_RETRY_DELAYS):
            delay_ms = int(self.AUTO_LOAD_RETRY_DELAYS[next_attempt] * 1000)
            QTimer.singleShot(
                delay_ms, lambda: self._attempt_auto_load(filepath, next_attempt)
            )
            return

        waited = sum(self.AUTO_LOAD_RETRY_DELAYS)
        detail = getattr(self, "_last_load_error", "") or "unknown error"
        QMessageBox.warning(
            self,
            "Could not display new file",
            f"{os.path.basename(filepath)} could not be read after {waited:.0f} seconds "
            f"of retrying.\n\nIt may still be transferring, or it may be incomplete. "
            f"Use Display → Redisplay image to try again.\n\nLast error: {detail}",
        )

    def on_batch_coalesced(self, suppressed, filepath):
        """A burst of files landed at once; only the newest of them was displayed.

        Surfaced so the skipped frames are visible as a deliberate choice rather than
        looking like the poller missed them.
        """
        plural = "s" if suppressed != 1 else ""
        self.statusBar().showMessage(
            f"{suppressed + 1} files arrived — showing the newest "
            f"({os.path.basename(filepath)}); {suppressed} older file{plural} skipped.",
            8000,
        )

