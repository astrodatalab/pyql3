import os
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QMenuBar, QMenu, QFileDialog, 
                               QMessageBox, QLabel)
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from pyql3.core.fits_reader import FitsReader
from pyql3.gui.dialogs.header_editor import HeaderEditorDialog
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.services.poller import DirectoryPoller
from pyql3.gui.dialogs.polling import PollingDialog
from pyql3.services.config import ConfigManager
from PySide6.QtCore import QTimer
import pyql3
from pyql3 import get_resource_path

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QuickLook 3")
        
        # Set application icon
        icon_path = get_resource_path("pyql3/icon.png")
        self.setWindowIcon(QIcon(icon_path))
        
        self.resize(600, 850)
        
        self.fits_reader = FitsReader()
        self.config = ConfigManager()
        
        # Setup Poller
        self.poller = DirectoryPoller(self)
        self.poller.file_detected.connect(self.on_file_detected)
        
        saved_poll_dir = self.config.get("polling_dir")
        if saved_poll_dir:
            self.poller.watch_path = saved_poll_dir
        
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
        
        self.create_menus()

    def create_menus(self):
        menubar = self.menuBar()
        
        # File Menu
        file_menu = menubar.addMenu("File")
        
        open_action = file_menu.addAction("Open...")
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_file)
        
        save_action = file_menu.addAction("Save FITS As...")
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_file_as)
        
        header_action = file_menu.addAction("Edit FITS Header")
        header_action.triggered.connect(self.edit_header)
        
        arith_action = file_menu.addAction("Arithmetic...")
        arith_action.triggered.connect(self.open_arithmetic_tool)
        
        polling_action = file_menu.addAction("Polling...")
        polling_action.triggered.connect(self.open_polling_config)
        
        file_menu.addSeparator()
        
        exit_action = file_menu.addAction("Exit")
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.setMenuRole(QAction.MenuRole.QuitRole)
        exit_action.triggered.connect(self.close)

        # Display Menu
        display_menu = menubar.addMenu("Display")
        
        redisplay_action = display_menu.addAction("Redisplay image")
        redisplay_action.triggered.connect(self.redisplay_image)
        
        rotate_action = display_menu.addAction("Rotate Image...")
        rotate_action.triggered.connect(self.open_rotate)
        
        display_menu.addSeparator()
        
        scaling_menu = display_menu.addMenu("Scaling")
        
        self.scale_action_group = QActionGroup(self)
        self.scale_actions = {}
        for scale_opt in ["Linear", "Negative", "HistEq", "Logarithmic", "Sqrt", "AsinH"]:
            act = scaling_menu.addAction(scale_opt)
            act.setCheckable(True)
            self.scale_action_group.addAction(act)
            self.scale_actions[scale_opt] = act
            act.triggered.connect(lambda checked, s=scale_opt: self.set_scaling(s))
            if scale_opt == "Linear":
                act.setChecked(True)
                
        display_menu.addSeparator()
        
        colormap_menu = display_menu.addMenu("Colormap")
        self.cmap_action_group = QActionGroup(self)
        self.cmap_actions = {}
        
        colormap_groups = [
            ("Perceptually Uniform", ["viridis", "plasma", "inferno", "magma", "cividis"]),
            ("Sequential", ["gray", "Blues", "YlOrBr", "hot"]),
            ("Scientific", ["cmc.oslo", "cmc.grayC", "cmc.devon", "cmc.lapaz", "cmc.vik", "cmc.roma"]),
            ("Diverging", ["RdBu", "coolwarm", "bwr", "seismic", "Spectral"])
        ]
        
        for i, (group_name, cmaps) in enumerate(colormap_groups):
            if i > 0:
                colormap_menu.addSeparator()
            for cmap in cmaps:
                act = colormap_menu.addAction(cmap)
                act.setCheckable(True)
                self.cmap_action_group.addAction(act)
                self.cmap_actions[cmap] = act
                act.triggered.connect(lambda checked, c=cmap: self.image_viewer.set_colormap(cmap_name=c))
                if cmap == "cmc.oslo":
                    act.setChecked(True)
                    
        invert_cmap_action = display_menu.addAction("Invert Colormap")
        invert_cmap_action.setCheckable(True)
        invert_cmap_action.triggered.connect(lambda checked: self.image_viewer.set_colormap(invert=checked))
                
        self.colorbar_action = display_menu.addAction("Show Colorbar")
        self.colorbar_action.setCheckable(True)
        self.colorbar_action.triggered.connect(self.image_viewer.toggle_colorbar)
        
        display_menu.addSeparator()
        
        self.pa_action = display_menu.addAction("Position Angle")
        self.pa_action.setCheckable(True)
        self.pa_action.triggered.connect(self.toggle_pa)
        
        units_menu = display_menu.addMenu("Data Units")
        self.unit_action_group = QActionGroup(self)
        
        self.action_dn_s = units_menu.addAction("As DN/s")
        self.action_dn_s.setCheckable(True)
        self.action_dn_s.setChecked(True)
        self.unit_action_group.addAction(self.action_dn_s)
        self.action_dn_s.triggered.connect(lambda: self.set_display_unit(False))
        
        self.action_tot_dn = units_menu.addAction("As Total DN")
        self.action_tot_dn.setCheckable(True)
        self.unit_action_group.addAction(self.action_tot_dn)
        self.action_tot_dn.triggered.connect(lambda: self.set_display_unit(True))
        
        # Sync scaling changes from viewer combo
        self.image_viewer.combo_scale.currentIndexChanged.connect(self.sync_scaling_from_viewer)

        # Plot Menu
        plot_menu = menubar.addMenu("Plot")
        
        depth_plot_action = plot_menu.addAction("Depth Plot")
        depth_plot_action.triggered.connect(self.open_depth_plot)
        
        hcut_action = plot_menu.addAction("Horizontal Cut")
        hcut_action.triggered.connect(self.open_horizontal_cut)
        
        vcut_action = plot_menu.addAction("Vertical Cut")
        vcut_action.triggered.connect(self.open_vertical_cut)
        
        dcut_action = plot_menu.addAction("Diagonal Cut")
        dcut_action.triggered.connect(self.open_diagonal_cut)
        
        surf_action = plot_menu.addAction("Surface")
        surf_action.triggered.connect(self.open_surface_plot)
        
        cont_action = plot_menu.addAction("Contour")
        cont_action.triggered.connect(self.open_contour_plot)
        
        plot_cat_action = plot_menu.addAction("Plot Catalog")
        plot_cat_action.triggered.connect(self.open_plot_catalog)
        
        # Analysis Menu
        analysis_menu = menubar.addMenu("Analysis")
        stats_action = analysis_menu.addAction("Statistics")
        stats_action.triggered.connect(self.open_statistics)
        phot_action = analysis_menu.addAction("Photometry")
        phot_action.triggered.connect(self.open_photometry)
        gauss_action = analysis_menu.addAction("Gaussian Fit")
        gauss_action.triggered.connect(self.open_gaussian_fit)
        
        # Strehl Ratio Tool
        action_strehl = QAction("Strehl Ratio", self)
        action_strehl.triggered.connect(self.open_strehl_tool)
        analysis_menu.addAction(action_strehl)
        
        # Removed Math Menu as Arithmetic was moved to File Menu
        
        # Help Menu
        help_menu = menubar.addMenu("Help")
        about_action = help_menu.addAction("About QuickLook 3")
        about_action.setMenuRole(QAction.MenuRole.AboutRole)
        about_action.triggered.connect(self.show_about)

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

    def open_file(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Open FITS File", "", "FITS Files (*.fits *.fit *.fits.gz);;All Files (*)")
        if filepath:
            self.load_fits(filepath)

    def load_fits(self, filepath, ext=None):
        try:
            self.fits_reader.load(filepath, ext=ext)
            data = self.fits_reader.get_data()
            if data is not None:
                header = self.fits_reader.get_header()
                self.image_viewer.set_data(data, header=header)
                self.setWindowTitle(f"QuickLook 3 - {filepath}")
                
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
                
            else:
                QMessageBox.warning(self, "Warning", "No valid data found in FITS file.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open FITS file:\n{str(e)}")


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
            self.setWindowTitle(f"QuickLook 3 - {title}")
            
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
        if self.fits_reader.filepath:
            ext_idx = self.fits_reader.current_ext
            self.load_fits(self.fits_reader.filepath, ext=ext_idx)
            
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
                
            # Update all open tools so they dynamically adjust their labels and values
            for attr in ['_depth_plot_dialog', '_hcut_dialog', '_vcut_dialog', '_dcut_dialog', '_strehl_dialog', '_stats_dialog', '_phot_dialog', '_gauss_dialog', '_plot_catalog_dialog']:
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

    def open_depth_plot(self):
        from pyql3.gui.tools.depth_plot import DepthPlotDialog
        if not hasattr(self, '_depth_plot_dialog') or not self._depth_plot_dialog.isVisible():
            self._depth_plot_dialog = DepthPlotDialog(self, self.image_viewer)
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
        self._surf_dialog.show()
        
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

    def open_gaussian_fit(self):
        from pyql3.gui.tools.fitting import GaussianFitDialog
        if not hasattr(self, '_gauss_dialog') or not self._gauss_dialog.isVisible():
            self._gauss_dialog = GaussianFitDialog(self, self.image_viewer)
        self._gauss_dialog.show()
        self._gauss_dialog.raise_()


    def open_arithmetic_tool(self):
        from .tools.arithmetic import ArithmeticDialog
        if not hasattr(self, 'arithmetic_dialog') or self.arithmetic_dialog is None:
            self.arithmetic_dialog = ArithmeticDialog(self, self.image_viewer)
        self.arithmetic_dialog.show()
        self.arithmetic_dialog.raise_()

    def open_strehl_tool(self):
        from pyql3.gui.tools.strehl import StrehlDialog
        self.strehl_dialog = StrehlDialog(self, self.image_viewer)
        self.strehl_dialog.show()

    def open_polling_config(self):
        dialog = PollingDialog(self.poller, self)
        if dialog.exec():
            # Save the active polling dir to config if started
            if self.poller.watch_path:
                self.config.set("polling_dir", self.poller.watch_path)
        
    def on_file_detected(self, filepath):
        # We add a small delay to ensure the file is completely written by the instrument
        QTimer.singleShot(500, lambda: self.load_fits(filepath))

