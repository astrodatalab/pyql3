"""The shared overlay-label policy.

`pyql3.gui.label_policy` holds no Qt import of its own, so everything here runs without a display
and without building a viewer. `QRectF` is imported only to have a rect to hand it.
"""

from PySide6.QtCore import QRectF

from pyql3.gui.label_policy import (
    LABEL_CULL_MARGIN,
    LABEL_SAFETY_LIMIT,
    LabelDensityGuard,
    grown_for_labels,
)


def test_the_cull_rect_is_grown_on_every_side():
    grown = grown_for_labels(QRectF(0.0, 0.0, 100.0, 200.0), margin=0.1)
    assert (grown.left(), grown.right()) == (-10.0, 110.0)
    assert (grown.top(), grown.bottom()) == (-20.0, 220.0)


def test_the_cull_rect_grows_by_its_own_size_not_a_fixed_amount():
    """The margin is a fraction, so a zoomed-in view is not given a huge apron."""
    small = grown_for_labels(QRectF(0.0, 0.0, 10.0, 10.0), margin=0.1)
    assert small.left() == -1.0


def test_no_rect_stays_no_rect():
    """A viewer that cannot be read yields None, and callers take that as 'do not cull'."""
    assert grown_for_labels(None) is None


def test_the_default_margin_is_used_when_none_is_given():
    grown = grown_for_labels(QRectF(0.0, 0.0, 100.0, 100.0))
    assert grown.left() == -100.0 * LABEL_CULL_MARGIN


def test_the_guard_allows_up_to_the_limit_and_refuses_past_it():
    guard = LabelDensityGuard()
    assert guard.allows(10, limit=10), "the limit itself must be allowed"
    assert not guard.allows(11, limit=10)


def test_the_guard_reports_only_when_the_verdict_changes():
    """A user told 'too many labels' must not be told again on every pan."""
    told = []
    guard = LabelDensityGuard(on_change=told.append)

    assert guard.allows(3, limit=5)
    assert told == [], "nothing to say while labels are being drawn"

    assert not guard.allows(9, limit=5)
    assert told == [9], "the offending count was not reported"

    assert not guard.allows(8, limit=5)
    assert told == [9], "reported again while already suppressing"

    assert guard.allows(2, limit=5)
    assert told == [9, 0], "the recovery was not reported"


def test_the_guard_tracks_whether_it_is_suppressing():
    guard = LabelDensityGuard()
    assert not guard.suppressing
    guard.allows(100, limit=5)
    assert guard.suppressing
    guard.allows(1, limit=5)
    assert not guard.suppressing


def test_the_limit_is_per_call_so_each_overlay_can_set_its_own():
    """The region layer and the catalog tool have different per-label costs, and the region
    layer's test monkeypatches its module constant after the layer is built."""
    guard = LabelDensityGuard()
    assert guard.allows(100, limit=1000)
    assert not guard.allows(100, limit=10)


def test_the_shared_default_limit_is_a_positive_count():
    assert isinstance(LABEL_SAFETY_LIMIT, int) and LABEL_SAFETY_LIMIT > 0
