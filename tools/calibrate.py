"""Measure the operating point (threshold and margin) from enrolled templates and probes.

    python tools/calibrate.py                       # target FAR 0.001, writes calibration/<ts>/
    python tools/calibrate.py --target-far 0.005 --margin-percentile 10 --dry-run

Inputs: templates from the database and probe crops from ``PROBES_DIR/<USN>/`` (made by
``tools/capture_probes.py``; never used as templates). Each probe is embedded and scored
with the *same* ``TemplateIndex`` the device uses: the genuine score is the probe against
its own student (max over templates), impostor scores are the probe against every
other student (max per student). Then ``common.face.calibration`` sweeps thresholds,
picks the operating point at the target FAR, reports the EER and the statistical limits
of a small demo (e.g. "0 false accepts in N trials"), and the chosen point is stored as
the active ``calibrations`` row that devices pick up on their next sync.

Outputs in ``calibration/<timestamp>/``: ``report.json``, ``report.md``, ``scores.csv``,
``histogram.png``, ``far_frr.png``, ``det.png``.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from common.face.calibration import OperatingPoint, SweepPoint, calibrate, sweep
from common.face.embedder import FaceEmbedder, bytes_to_embedding
from common.face.matcher import Template, TemplateIndex
from common.face.types import Array
from server.app.config import Settings
from server.app.db import make_engine, make_session_factory
from server.app.face import build_engine
from server.app.models import Calibration, Student
from server.app.services import audit

log = logging.getLogger("calibrate")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


@dataclass(frozen=True)
class ProbeScore:
    usn: str
    file: str
    genuine: float
    best_other_usn: str
    best_other: float

    @property
    def gap(self) -> float:
        return self.genuine - self.best_other


@dataclass
class ScoreSet:
    genuine: list[float]
    impostor: list[float]
    gaps: list[float]
    probes: list[ProbeScore]
    skipped: list[tuple[str, str]]  # (file, reason)


def load_index(db: Session, model_version: str) -> TemplateIndex:
    students = list(
        db.scalars(select(Student).options(selectinload(Student.templates)).where(Student.active))
    )
    templates = [
        Template(usn=s.usn, name=s.name, embedding=bytes_to_embedding(t.embedding))
        for s in students
        for t in s.templates
        if t.model_version == model_version
    ]
    return TemplateIndex(templates)


def iter_probes(probes_dir: Path) -> list[tuple[str, Path]]:
    pairs: list[tuple[str, Path]] = []
    if not probes_dir.is_dir():
        return pairs
    for folder in sorted(p for p in probes_dir.iterdir() if p.is_dir()):
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        pairs.extend((folder.name.upper(), file) for file in files)
    return pairs


def score_probes(
    index: TemplateIndex, embedder: FaceEmbedder, probes: list[tuple[str, Path]]
) -> ScoreSet:
    """Embed each probe crop and score it exactly like the device would."""
    result = ScoreSet([], [], [], [], [])
    usns = index.usns
    position = {usn: i for i, usn in enumerate(usns)}
    for usn, file in probes:
        if usn not in position:
            result.skipped.append((str(file), "no templates for this USN"))
            continue
        crop: Array | None = cv2.imread(str(file))
        if crop is None or crop.shape[:2] != (112, 112):
            result.skipped.append((str(file), "not a 112x112 crop"))
            continue
        embedding = embedder.embed(crop)
        scores = index.student_scores(embedding)
        own = position[usn]
        genuine = float(scores[own])
        others = np.delete(scores, own)
        if others.size == 0:
            result.skipped.append((str(file), "only one enrolled student; no impostors"))
            continue
        other_names = [u for i, u in enumerate(usns) if i != own]
        best_i = int(np.argmax(others))
        result.genuine.append(genuine)
        result.impostor.extend(float(v) for v in others)
        result.gaps.append(genuine - float(others[best_i]))
        result.probes.append(
            ProbeScore(usn, file.name, genuine, other_names[best_i], float(others[best_i]))
        )
    return result


# ------------------------------------------------------------------ reporting
def write_plots(
    out_dir: Path, scores: ScoreSet, points: list[SweepPoint], op: OperatingPoint
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bins = np.linspace(-0.2, 1.0, 61)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(
        scores.impostor,
        bins=bins,
        alpha=0.6,
        label=f"impostor (n={len(scores.impostor)})",
        color="#b71c1c",
    )
    ax.hist(
        scores.genuine,
        bins=bins,
        alpha=0.6,
        label=f"genuine (n={len(scores.genuine)})",
        color="#1b5e20",
    )
    ax.axvline(op.threshold, color="k", linestyle="--", label=f"threshold {op.threshold:.3f}")
    ax.set_xlabel("cosine similarity")
    ax.set_ylabel("count")
    ax.set_title("Genuine vs impostor scores")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "histogram.png", dpi=120)
    plt.close(fig)

    thresholds = [p.threshold for p in points]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(thresholds, [p.far for p in points], label="FAR", color="#b71c1c")
    ax.plot(thresholds, [p.frr for p in points], label="FRR", color="#1b5e20")
    ax.axvline(op.threshold, color="k", linestyle="--", label=f"chosen {op.threshold:.3f}")
    ax.axvline(
        op.eer_threshold,
        color="grey",
        linestyle=":",
        label=f"EER {op.eer:.3%} @ {op.eer_threshold:.3f}",
    )
    ax.set_xlabel("threshold")
    ax.set_ylabel("rate")
    ax.set_ylim(0, 1)
    ax.set_title("FAR and FRR versus threshold")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "far_frr.png", dpi=120)
    plt.close(fig)

    fars = np.array([max(p.far, 1e-6) for p in points])
    frrs = np.array([max(p.frr, 1e-6) for p in points])
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.loglog(fars, frrs, color="#1d4ed8")
    ax.scatter([max(op.far, 1e-6)], [max(op.frr, 1e-6)], color="k", zorder=3, label="chosen point")
    ax.set_xlabel("FAR")
    ax.set_ylabel("FRR")
    ax.set_title("DET curve")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "det.png", dpi=120)
    plt.close(fig)


def write_report(
    out_dir: Path,
    scores: ScoreSet,
    points: list[SweepPoint],
    op: OperatingPoint,
    model_version: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": model_version,
        "operating_point": asdict(op),
        "sweep": [asdict(p) for p in points],
        "skipped": scores.skipped,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with (out_dir / "scores.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["usn", "file", "genuine", "best_other_usn", "best_other", "gap"])
        for p in scores.probes:
            writer.writerow(
                [
                    p.usn,
                    p.file,
                    f"{p.genuine:.4f}",
                    p.best_other_usn,
                    f"{p.best_other:.4f}",
                    f"{p.gap:.4f}",
                ]
            )
    false_accepts = int(sum(1 for s in scores.impostor if s >= op.threshold))
    lines = [
        f"# Calibration report ({model_version})",
        "",
        "| Quantity | Value |",
        "|---|---|",
        f"| Threshold (target FAR {op.target_far:g}) | **{op.threshold:.3f}** |",
        f"| Margin (low percentile of genuine gap) | **{op.margin:.3f}** |",
        (
            f"| Measured FAR | {op.far:.4%} "
            f"({false_accepts} false accepts in {op.n_impostor} impostor trials) |"
        ),
        (
            f"| Measured FRR | {op.frr:.2%} "
            f"({round(op.frr * op.n_genuine)} of {op.n_genuine} genuine probes rejected) |"
        ),
        f"| 95% upper bound on FAR | {op.far_upper_bound_95:.3g} |",
        f"| EER | {op.eer:.3%} at threshold {op.eer_threshold:.3f} |",
        f"| Genuine / impostor trials | {op.n_genuine} / {op.n_impostor} |",
        "",
    ]
    if op.warnings:
        lines += ["## Statistical limits", ""] + [f"- {w}" for w in op.warnings] + [""]
    if scores.skipped:
        lines += ["## Skipped probes", ""] + [f"- `{f}`: {why}" for f, why in scores.skipped] + [""]
    lines += [
        "Plots: `histogram.png`, `far_frr.png`, `det.png`. Per-probe scores: `scores.csv`.",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def activate_calibration(
    db: Session,
    op: OperatingPoint,
    model_version: str,
    report_path: str,
    *,
    actor_id: int | None = None,
) -> Calibration:
    """Insert the new row as the only active one for this model. Commits."""
    for previous in db.scalars(
        select(Calibration).where(
            Calibration.model_version == model_version, Calibration.active.is_(True)
        )
    ):
        previous.active = False
    db.flush()
    row = Calibration(
        model_version=model_version,
        threshold=op.threshold,
        margin=op.margin,
        target_far=op.target_far,
        measured_far=op.far,
        measured_frr=op.frr,
        eer=op.eer,
        n_genuine=op.n_genuine,
        n_impostor=op.n_impostor,
        report_path=report_path,
        active=True,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        actor_id=actor_id,
        action="calibration.activate",
        entity="calibration",
        entity_id=row.id,
        after={
            "model_version": model_version,
            "threshold": op.threshold,
            "margin": op.margin,
            "far": op.far,
            "frr": op.frr,
            "n_genuine": op.n_genuine,
            "n_impostor": op.n_impostor,
        },
    )
    db.commit()
    return row


def run_calibration(
    db: Session,
    settings: Settings,
    embedder: FaceEmbedder,
    *,
    target_far: float,
    margin_percentile: float,
    out_root: Path | None = None,
    activate: bool = True,
) -> tuple[OperatingPoint, Path, ScoreSet]:
    index = load_index(db, settings.model_version)
    if index.n_students < 2:
        raise SystemExit("Need templates for at least two students before calibrating.")
    probes = iter_probes(settings.probes_dir)
    if not probes:
        raise SystemExit(
            f"No probe crops under {settings.probes_dir}. Run tools/capture_probes.py first."
        )
    scores = score_probes(index, embedder, probes)
    op = calibrate(
        np.array(scores.genuine),
        np.array(scores.impostor),
        np.array(scores.gaps),
        target_far=target_far,
        margin_percentile=margin_percentile,
    )
    points = sweep(np.array(scores.genuine), np.array(scores.impostor))
    out_dir = (out_root or settings.calibration_dir) / datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    write_report(out_dir, scores, points, op, settings.model_version)
    write_plots(out_dir, scores, points, op)
    if activate:
        activate_calibration(db, op, settings.model_version, str(out_dir / "report.md"))
    return op, out_dir, scores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--target-far", type=float, default=0.001)
    parser.add_argument("--margin-percentile", type=float, default=5.0)
    parser.add_argument(
        "--dry-run", action="store_true", help="write the report but do not activate it"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    settings = Settings()
    engine = build_engine(settings)
    factory = make_session_factory(make_engine(settings.database_url))
    with factory() as db:
        _, out_dir, _ = run_calibration(
            db,
            settings,
            engine.embedder,
            target_far=args.target_far,
            margin_percentile=args.margin_percentile,
            activate=not args.dry_run,
        )
    print((out_dir / "report.md").read_text(encoding="utf-8"))
    print(f"report written to {out_dir}")
    if args.dry_run:
        print("dry run: calibration NOT activated")
    else:
        print("activated: devices receive the new threshold/margin on their next sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
