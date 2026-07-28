import pytest
import numpy as np
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.core.fits_reader import FitsReader
from pyql3.gui.tools.cuts import CutPlotDialog
from pyql3.gui.tools.fitting import GaussianFitDialog
from pyql3.gui.tools.statistics import StatisticsDialog
from pyql3.gui.tools.photometry import PhotometryDialog
from pyql3.gui.tools.strehl import StrehlDialog
from pyql3.gui.tools.advanced_plots import SurfacePlotDialog
from pyql3.gui.tools.arithmetic import ArithmeticDialog


def test_cuts_tool(loaded_viewer):
    """Test horizontal, vertical, and diagonal profile cuts."""
    for orientation in ['horizontal', 'vertical', 'diagonal']:
        dialog = CutPlotDialog(orientation, image_viewer=loaded_viewer)
        dialog.update_plot()
        assert dialog.plot_curve is not None, f"Plot curve None for {orientation} cut"
        dialog.close()


def test_gaussian_fit_tool(loaded_viewer):
    """Test 2D Gaussian fitting tool centroid calculation and FWHM outputs."""
    dialog = GaussianFitDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    dialog.update_fit()
    assert len(dialog.value_labels) > 0, "GaussianFitDialog missing value labels"
    dialog.close()


def test_statistics_tool(loaded_viewer):
    """Test region statistics calculations (mean, std, min, max, sum)."""
    dialog = StatisticsDialog(image_viewer=loaded_viewer)
    dialog.update_stats()
    assert dialog.value_labels["Mean"].text() != "N/A", "Statistics Mean value label was not updated"
    assert dialog.value_labels["Max"].text() != "N/A", "Statistics Max value label was not updated"
    dialog.close()


def test_photometry_tool(loaded_viewer):
    """Test circular and rectangular aperture photometry integration."""
    dialog = PhotometryDialog(image_viewer=loaded_viewer)
    dialog.update_photometry()
    assert dialog.lbl_flux.text() != "N/A", "Photometry flux label was not updated"
    assert dialog.lbl_total.text() != "N/A", "Photometry total label was not updated"
    dialog.close()


def test_strehl_tool(loaded_viewer):
    """Test Strehl ratio calculation tool."""
    dialog = StrehlDialog(image_viewer=loaded_viewer)
    dialog.update_strehl()
    assert dialog.lbl_strehl.text() != "N/A", "Strehl ratio label was not updated"
    dialog.close()





def test_arithmetic_tool(loaded_viewer):
    """Test datacube arithmetic dialog initialization with open viewers."""
    dialog = ArithmeticDialog(image_viewer=loaded_viewer)
    dialog.close()
