"""Measure a rendered synthetic 96-well scan and Qt event-loop latency.

Run with ``uv run examples/benchmark_stage_explorer.py`` on a desktop (OpenGL
required). Uses 672 independent 2048x2048 uint16 fields, about 5.25 GiB of source
data, at a nominal 10 ms frame interval. Seven fields per well, 9 mm well pitch,
1.625 um pixels. Pans throughout acquisition, then zooms and pans for six seconds.
The 5 ms heartbeat measures event-loop gaps, including rendering/driver stalls;
it is not an OS mouse-input latency measurement. Run against each revision in
otherwise idle environments to compare. No microscope or acquisition engine is
involved. Timings include immutable source copies in versions that make them.
"""

from __future__ import annotations

import json
import math
import platform
from time import perf_counter
from typing import Any

import numpy as np
import psutil
from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QApplication

from pymmcore_widgets.control._stage_explorer import _overview
from pymmcore_widgets.control._stage_explorer._stage_viewer import StageViewer


def main() -> None:
    """Run the scan, printing timing percentiles and peak resident memory."""
    app = QApplication.instance() or QApplication([])
    stats: dict[str, list[float]] = {}

    def instrument(cls: Any, name: str) -> None:
        original = getattr(cls, name)

        def measured(*args: Any, **kwargs: Any) -> Any:
            started = perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                stats.setdefault(name, []).append((perf_counter() - started) * 1000)

        setattr(cls, name, measured)

    for name in ("cull_to_view", "add_image", "apply_downsampling"):
        instrument(StageViewer, name)
    instrument(
        StageViewer,
        "_process_images"
        if hasattr(StageViewer, "_process_images")
        else "_paint_overview",
    )
    instrument(getattr(_overview, "OverviewData", _overview.OverviewPyramid), "_build")
    viewer = StageViewer()
    viewer.resize(1000, 700)
    viewer.set_clims((0, 65535))
    viewer.show()
    source = np.random.default_rng(0).integers(0, 65536, (2048, 2048), dtype=np.uint16)
    positions = []
    for row in range(8):
        for col in range(12):
            for field in range(7):
                angle = (field - 1) * math.pi / 3
                dx, dy = (
                    (0, 0)
                    if field == 0
                    else (2400 * math.cos(angle), 2400 * math.sin(angle))
                )
                positions.append((col * 9000 + dx, row * 9000 + dy))

    started = last_beat = perf_counter()
    gaps = []
    index = 0
    completed: float | None = None
    peak_rss = 0
    process = psutil.Process()
    heartbeat_timer, frame_timer, camera_timer = QTimer(), QTimer(), QTimer()

    def heartbeat() -> None:
        nonlocal last_beat, peak_rss
        now = perf_counter()
        gaps.append((now - last_beat) * 1000)
        last_beat = now
        peak_rss = max(peak_rss, process.memory_info().rss)

    def frame() -> None:
        nonlocal index, completed
        if index == len(positions):
            frame_timer.stop()
            completed = perf_counter()
            return
        x, y = positions[index]
        matrix = np.diag([1.625, 1.625, 1.0, 1.0])
        matrix[:2, 3] = (x - 1664, y - 1664)
        viewer.add_image(source.copy(), matrix)
        index += 1

    def summary(values: list[float]) -> dict[str, Any]:
        return {
            "total_ms": round(sum(values), 2),
            "count": len(values),
            "p50_p95_p99_max_ms": [
                round(float(v), 2) for v in np.percentile(values, [50, 95, 99, 100])
            ],
        }

    def move() -> None:
        now = perf_counter()
        elapsed = now - started
        width = 110000 if completed is None or now - completed >= 3 else 22000
        x = 45000 + 700 * math.sin(elapsed)
        viewer.view.camera.set_range(
            x=(x - width / 2, x + width / 2),
            y=(30000 - width * 0.35, 30000 + width * 0.35),
            margin=0,
        )
        if (completed is not None and now - completed > 6) or elapsed > 90:
            for timer in (heartbeat_timer, frame_timer, camera_timer):
                timer.stop()
            print(
                json.dumps(
                    {
                        "platform": platform.platform(),
                        "fields": index,
                        "shape": list(source.shape),
                        "dtype": str(source.dtype),
                        "elapsed_s": round(elapsed, 2),
                        "scan_s": round(completed - started, 2) if completed else None,
                        "heartbeat": summary(gaps),
                        "paths": {k: summary(v) for k, v in stats.items()},
                        "peak_rss_GiB": round(peak_rss / 2**30, 2),
                        "map_source_GiB": round(
                            sum(t.data.nbytes for t in viewer._tiles) / 2**30, 2
                        ),
                        "overview_MiB": round(viewer.overview_nbytes() / 2**20, 2),
                    },
                    indent=2,
                ),
                flush=True,
            )
            viewer.close()
            app.quit()

    for timer, interval, callback in (
        (heartbeat_timer, 5, heartbeat),
        (frame_timer, 10, frame),
        (camera_timer, 40, move),
    ):
        timer.setInterval(interval)
        timer.timeout.connect(callback)
        timer.start()
    app.exec()


if __name__ == "__main__":
    main()
