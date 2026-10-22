"""Taking graphics items off a pyqtgraph scene for good.

The tool dialogs (`BaseToolDialog.retire_roi`) and the region layer (`RegionLayer._destroy_items`)
both take items off the image, keep them in a list until the event loop is back, and then let
them go (`BUGS.md` M18). These are the two steps they share: before an item is deleted, the scene
must stop pointing at it; and when it is let go, it is deleted outright rather than left to the
collector (`BUGS.md` M31, R89).
"""
import shiboken6


def release_scene_references(scene, item):
    """Make `scene` forget `item` and its descendants (an ROI's handles) before it is deleted.

    pyqtgraph's `GraphicsScene` holds the item being dragged (`dragItem`) and the last hover
    event, whose click and drag claims name the item under the cursor, which is often a handle.
    Once the item is deleted, the next click reached the deleted object through those
    references (`BUGS.md` M31).

    `dragItem` is cleared only when it is `item` or one of its descendants. The last hover event
    is dropped whole when any of its click or drag claims names one of them, which also forgets
    the claims of other items under the cursor; with no hover event the scene finds the item for
    the next press by position (`itemsNearEvent`), and the next mouse move records a new one.
    """
    if scene is None or not shiboken6.isValid(item):
        return

    def is_part_of_item(other):
        return other is item or (other is not None and shiboken6.isValid(other)
                                 and item.isAncestorOf(other))

    if is_part_of_item(getattr(scene, 'dragItem', None)):
        scene.dragItem = None
    hover = getattr(scene, 'lastHoverEvent', None)
    if hover is not None:
        claimed = list(hover.clickItems().values()) + list(hover.dragItems().values())
        if any(is_part_of_item(other) for other in claimed):
            scene.lastHoverEvent = None


def delete_items(items):
    """Delete each of `items` whose C++ object still exists.

    Left to `gc.collect()`, retired ROIs and their handles have crashed the collection in
    `callCppDestructor` (`BUGS.md` M31, R89). The crash appeared or not depending on unrelated
    allocations, and the mechanism is not established. After this, the items and their
    children report `shiboken6.isValid` False (asserted by
    `tests/test_region_layer.py::test_retired_items_are_deleted_before_they_are_collected`), so
    the collector never destroys a C++ object itself.
    """
    for item in items:
        if shiboken6.isValid(item):
            shiboken6.delete(item)
