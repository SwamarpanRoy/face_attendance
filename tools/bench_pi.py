"""Latency benchmark for the recognition pipeline (Pi 4 or laptop).

    .venv/bin/python tools/bench_pi.py                       # Pi camera, 60 iterations
    python tools/bench_pi.py --image tests/fixtures/faces/portrait_2.jpg --sim
    python tools/bench_pi.py --detector-input 480 --threads 4 --json bench.json

Reports p50/p95 in milliseconds for capture, detect, align, embed, match and end to end,
plus CPU temperature and the firmware throttling flags (``vcgencmd get_throttled``).
"match" is measured against a synthetic index of 2,000 students x 3 templates, the
design scale, so the number is independent of how many students are enrolled.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import numpy as np

from common.face.detector import DetectorConfig
from common.face.embedder import EmbedderConfig
from common.face.engine import EngineConfig, FaceEngine
from common.face.matcher import Template, TemplateIndex
from common.version import __version__
from device.app import sysinfo
from device.app.camera import make_source
from device.app.config import DeviceConfig


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    rank = max(1, int(np.ceil(q * len(ordered))))
    return ordered[rank - 1]


def synthetic_index(students: int = 2000, per_student: int = 3) -> TemplateIndex:
    rng = np.random.default_rng(0)
    templates = []
    for s in range(students):
        base = rng.normal(size=512).astype(np.float32)
        templates.extend(
            Template(
                usn=f"S{s:05d}",
                name="x",
                embedding=base + rng.normal(scale=0.01, size=512).astype(np.float32),
            )
            for _ in range(per_student)
        )
    return TemplateIndex(templates)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--iterations", type=int, default=60)
    parser.add_argument("--detector-input", type=int, default=None, help="override device.toml")
    parser.add_argument("--threads", type=int, default=None, help="override onnx_threads")
    parser.add_argument(
        "--image", type=Path, default=None, help="benchmark on a still image instead of the camera"
    )
    parser.add_argument("--sim", action="store_true", help="use the laptop webcam")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--json", type=Path, default=None, help="write results to this file")
    args = parser.parse_args(argv)

    config = DeviceConfig.load(args.config)
    rec = config.recognition
    engine_config = EngineConfig(
        detector=DetectorConfig(
            input_size=args.detector_input or rec.detector_input,
            threads=args.threads or rec.onnx_threads,
        ),
        quality=config.engine_config().quality,
        embedder=EmbedderConfig(threads=args.threads or rec.onnx_threads),
    )
    t = time.perf_counter()
    engine = FaceEngine(config.models_dir, engine_config)
    load_ms = (time.perf_counter() - t) * 1000
    index = synthetic_index()
    source = make_source(
        sim=args.sim,
        webcam_index=config.camera.sim_webcam_index,
        size=config.camera.main_size,
        image=args.image,
    )
    source.start()

    stages: dict[str, list[float]] = {
        k: [] for k in ("capture", "detect", "align", "embed", "match", "total")
    }
    gated = 0
    try:
        for _ in range(3):  # warm-up
            frame = source.read()
            if frame is not None:
                engine.process(frame)
        for _ in range(args.iterations):
            t0 = time.perf_counter()
            frame = source.read()
            t1 = time.perf_counter()
            if frame is None:
                continue
            result = engine.process(frame)
            t2 = time.perf_counter()
            stages["capture"].append((t1 - t0) * 1000)
            stages["detect"].append(result.timings_ms.get("detect", float("nan")))
            if result.embedding is not None:
                gated += 1
                stages["align"].append(result.timings_ms.get("align", float("nan")))
                stages["embed"].append(result.timings_ms.get("embed", float("nan")))
                t3 = time.perf_counter()
                index.match(result.embedding)
                stages["match"].append((time.perf_counter() - t3) * 1000)
                stages["total"].append((t2 - t0) * 1000 + stages["match"][-1])
    finally:
        source.stop()

    summary = {
        stage: {
            "p50": round(percentile(v, 0.5), 1),
            "p95": round(percentile(v, 0.95), 1),
            "n": len(v),
        }
        for stage, v in stages.items()
    }
    report = {
        "version": __version__,
        "machine": platform.machine(),
        "python": platform.python_version(),
        "detector_input": engine_config.detector.input_size,
        "threads": engine_config.detector.threads,
        "model_load_ms": round(load_ms),
        "iterations": args.iterations,
        "gated_frames": gated,
        "stages_ms": summary,
        "cpu_temp_c": sysinfo.cpu_temp_c(),
        "throttled": sysinfo.throttled_state(),
        "free_mem_mb": sysinfo.free_mem_mb(),
        "source": source.description,
    }
    print(f"face-attendance bench {__version__} on {report['machine']} python {report['python']}")
    print(
        f"detector_input={report['detector_input']} threads={report['threads']} "
        f"model load {report['model_load_ms']} ms; "
        f"{gated}/{args.iterations} frames passed the gates"
    )
    print(f"{'stage':8s} {'p50 ms':>8s} {'p95 ms':>8s} {'n':>4s}")
    for stage, s in summary.items():
        print(f"{stage:8s} {s['p50']:8.1f} {s['p95']:8.1f} {s['n']:4d}")
    print(
        f"CPU temp: {report['cpu_temp_c']}  throttled: {report['throttled']}  "
        f"free mem: {report['free_mem_mb']} MB"
    )
    budget = summary["total"]["p95"]
    print(
        "BUDGET OK (p95 end-to-end < 2000 ms)"
        if budget == budget and budget < 2000
        else "BUDGET MISSED or no gated frames"
    )
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"written {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
