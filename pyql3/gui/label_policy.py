"""When overlay labels are worth drawing.

Text is the most expensive thing either overlay paints, so both the region layer and the catalog
tool hide labels while the view moves, cull them to what is on screen, and refuse to build an
enormous set at once. That policy was written twice — and the second copy was already missing two
thirds of it — so it lives here once, imported by both.

Deliberately holds no Qt import: `grown_for_labels` works through the rect it is handed, and the
guard is arithmetic plus a callback. Both are therefore testable without a display.

What stays with each tool is **making the items**, which has nothing in common between the two: a
region's label carries that region's colour, font and offset, while a catalog's carries a column
value. Only the policy is shared, and each caller passes its own ceiling — the two overlays have
different per-label costs and different reasons for the counts they see.
"""

#: How long after panning stops before labels come back, in milliseconds.
#:
#: Text is the most expensive thing on an overlay to paint: measured at 34.9 ms per pan frame for
#: 400 labelled regions against 25.4 ms with the labels hidden — 27% of the frame. The catalog
#: tool found the same and hides its labels the same way, with the same delay.
LABEL_REDRAW_DELAY_MS = 200

#: The visible rect is grown by this fraction before culling labels, so text just off the edge —
#: which still paints into the view — is not dropped.
LABEL_CULL_MARGIN = 0.1

#: A ceiling on labels built at once, to stop an enormous set from locking the window up.
#:
#: This is a hang guard, not a judgement about readability: whether a crowd of labels is useful is
#: the user's call, made with **Region ➔ Show Region Labels**, exactly as the catalog tool offers
#: a *Show Names* checkbox. Labels are culled to the visible rect and hidden while panning, so the
#: cost of a large set falls on the redraw after the view settles — measured at ~0.18 ms per
#: region label, so this ceiling is about a second in the worst case.
#:
#: This is the default for a caller that has not measured its own cost. The catalog tool has, and
#: passes its own (`plot_catalog.CATALOG_LABEL_LIMIT`) — its labels are built fresh from a table
#: rather than toggled, and it can be asked for 66,196 of them at once.
LABEL_SAFETY_LIMIT = 5000


def grown_for_labels(rect, margin=LABEL_CULL_MARGIN):
    """`rect` grown by `margin` of its own size on every side, or None if there is no rect.

    Labels are culled against this rather than the exact visible rect because a label anchored
    just outside the view still paints into it, so culling on the exact rect makes text at the
    edges flicker in and out as the user pans.
    """
    if rect is None:
        return None
    dx = rect.width() * margin
    dy = rect.height() * margin
    return rect.adjusted(-dx, -dy, dx, dy)


class LabelDensityGuard:
    """Applies a ceiling to a label count, and notices when the answer changes.

    Kept as an object purely for that second part: an overlay decides whether to draw labels on
    every redraw, and a user who has just been told "too many labels" does not want to be told
    again on each pan. `on_change(count)` is called only when the verdict flips — with the
    offending count when labels start being refused, and with 0 when they are allowed again — so
    a caller can drive a status line or a Qt signal from it directly.

    The ceiling is passed to `allows` rather than held here, so that a caller can vary it (and so
    that a test can monkeypatch the module constant it comes from).
    """

    def __init__(self, on_change=None):
        self._on_change = on_change
        self._suppressing = False

    @property
    def suppressing(self):
        """True while the ceiling is refusing to draw labels."""
        return self._suppressing

    def allows(self, count, limit):
        """True if `count` labels may be built at once."""
        too_many = count > limit
        if too_many != self._suppressing:
            self._suppressing = too_many
            if self._on_change is not None:
                self._on_change(count if too_many else 0)
        return not too_many
