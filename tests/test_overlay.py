import numpy as np

from adbbot.core.events import DetectionsUpdated, EventBus
from adbbot.core.types import Detection, Match, Rect
from adbbot.vision.overlay import DetectionStore
from tests.imagegen import textured


def test_add_publishes_snapshot(bus: EventBus) -> None:
    events: list[DetectionsUpdated] = []
    bus.subscribe(DetectionsUpdated, events.append)
    store = DetectionStore(bus)
    store.new_frame(1.0)

    store.found(Match(Rect(0, 0, 10, 10), 0.93, "home/a"), 1.0)
    store.missing(Rect(0, 0, 50, 50), "home/b", 1.0, confidence=0.4)

    assert [len(e.detections) for e in events] == [1, 2]
    assert events[-1].frame_ts == 1.0
    assert [d.kind for d in store.detections] == ["found", "missing"]


def test_frames_replace_detections() -> None:
    store = DetectionStore()
    store.new_frame(1.0)
    store.mark(Rect(0, 0, 1, 1), "a")
    store.new_frame(2.0)
    assert store.detections == []

    store.mark(Rect(0, 0, 1, 1), "old", frame_ts=1.0)  # älterer Frame → verworfen
    assert store.detections == []

    store.mark(Rect(0, 0, 1, 1), "b")
    store.mark(Rect(0, 0, 1, 1), "newer", frame_ts=3.0)  # neuerer Frame → ersetzt
    assert [d.label for d in store.detections] == ["newer"]
    assert store.frame_ts == 3.0


def test_max_items() -> None:
    store = DetectionStore(max_items=3)
    for i in range(5):
        store.add(Detection(Rect(0, 0, 1, 1), str(i), "mark", 1.0))
    assert [d.label for d in store.detections] == ["2", "3", "4"]


def test_render_draws_on_a_copy() -> None:
    image = textured(200, 300, seed=1)
    original = image.copy()
    store = DetectionStore()
    store.new_frame(1.0)

    assert np.array_equal(store.render(image), image)

    store.found(Match(Rect(20, 50, 60, 40), 0.9, "home/ä"), 1.0)
    store.missing(Rect(10, 150, 100, 60), "home/b", 1.0, 0.5)
    store.text(Rect(0, 0, 80, 20), "Gold 12", 1.0, 0.8)
    rendered = store.render(image)

    assert np.array_equal(image, original)
    assert rendered.shape == image.shape
    assert not np.array_equal(rendered[50:90, 20:80], image[50:90, 20:80])
    assert not np.array_equal(rendered[150:210, 10:110], image[150:210, 10:110])
