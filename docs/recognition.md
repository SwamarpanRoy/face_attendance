# Recognition core (`common/face/`)

The same code runs on the Pi, in the laptop simulator, in the enrolment tools and in
the calibration tool, so an embedding means the same thing everywhere.

## Pipeline

```
frame (BGR) ─► detector (det_500m, SCRFD) ─► gates ─► align 112×112 ─► blur gate
             ─► liveness (no-op) ─► embedder (w600k_mbf, 512-d, L2) ─► matcher
```

| Stage | Module | Notes |
|---|---|---|
| Detect | `detector.py` | Letterbox to `detector_input` (320 default, 480/640 for small faces), `onnxruntime` with `intra_op_num_threads`, anchor decode for strides 8/16/32 × 2 anchors, greedy NMS at IoU 0.4. |
| Gates | `quality.py` | exactly one face → "One person at a time"; width ≥ `min_face_width_px` → "Move closer"; yaw estimate ≤ `max_yaw_deg` → "Look at the camera"; Laplacian variance of the aligned crop ≥ `blur_min` → "Hold still". |
| Align | `align.py` | Umeyama similarity transform of the 5 landmarks onto the ArcFace template; numpy port of scikit-image's estimator. |
| Liveness | `liveness.py` | `LivenessCheck` protocol, `NoOpLiveness` for now. |
| Embed | `embedder.py` | `(x − 127.5) / 127.5`, RGB, 112×112 → 512-d, L2-normalised. Byte layout: float32 little-endian, 2048 bytes (server `BYTEA` and device SQLite). |
| Match | `matcher.py` | `scores = T @ e`, per-student max; accept if `best ≥ threshold` **and** `best − second ≥ margin`; a student is marked when the same USN wins 2 of the last 3 gated frames; once per session (cooldown). |

`engine.py` wires it together (`FaceEngine.process` for live frames, `embed_single` for
enrolment photos) and returns per-stage timings that `tools/bench_pi.py` aggregates.

## Thresholds and calibration (M5)

`threshold` and `margin` are **measured**, not guessed. Until a calibration exists the
device uses `fallback_threshold` / `fallback_margin` from `device.toml` and shows
"not calibrated".

1. **Probes.** `python tools/capture_probes.py --usn <USN> --count 4` (webcam window) or
   `--from-folder photos/` (files `<USN>_x.jpg` or `<USN>/*.jpg`) stores aligned
   112×112 test crops under `data/probes/<USN>/`. On a Pi add `--upload <server> --token
   <device token>`. Probes need the student's existing consent, are taken on another
   day or under other light where possible, and are never used as templates.
2. **Calibrate.** `python tools/calibrate.py [--target-far 0.001] [--dry-run]` embeds the
   probes and scores them with the same `TemplateIndex` the device runs: genuine =
   probe vs own student (max over templates), impostor = probe vs every other student
   (max per student). `common/face/calibration.py` sweeps thresholds 0..1 in steps of
   0.005, picks the lowest threshold whose FAR ≤ target (lowest FRR for that budget),
   reports the EER, and sets `margin` to the 5th percentile of (own − best other) over
   genuine probes.
3. **Outputs.** `calibration/<timestamp>/report.md` (table: threshold, margin, FAR, FRR,
   EER, trial counts, 95% upper bound on FAR by the rule of three), `report.json`,
   `scores.csv`, `histogram.png`, `far_frr.png`, `det.png`, and a new active row in
   `calibrations` that devices receive on their next sync.

With 10–20 demo students there are only a few hundred impostor pairs, so a FAR of
0.001 cannot be measured directly; the report says so explicitly ("0 false accepts in
N trials, 95% upper bound 3/N") rather than pretending otherwise.

## Verification against the reference implementation

The port was compared with InsightFace's own `scrfd.py`, `face_align.py` and
`arcface_onnx.py` on the two public-domain portraits in `tests/fixtures/faces/`:

| Quantity | Difference |
|---|---|
| boxes, scores, landmarks at 320 / 480 / 640 | identical (0.0) |
| alignment matrix | ≤ 8e-6 |
| aligned 112×112 crop | ≤ 1 grey level |
| embedding (cosine to reference) | 0.9999999 |

`tests/common/test_models_real.py` keeps guarding the behaviour (one face found,
landmarks inside the box, unit-norm embedding, same person ≥ 0.6 under a flip,
different people < 0.35, the recogniser marks the enrolled person and rejects a
stranger). It is skipped when `models/` is empty.

## Latency (development laptop, one portrait, 20 frames)

| `detector_input` | ms per frame |
|---|---|
| 320 | 13 |
| 480 | 16 |
| 640 | 27 |

Pi 5 numbers come from `tools/bench_pi.py` (M8); the budget is 2 s end to end.

## Laptop simulator

```
python -m device.app.main --sim                                   # webcam window
python -m device.app.main --sim --image tests/fixtures/faces/portrait_2.jpg --enrol-test "1BM22EC001:Aditi Rao"
python -m device.app.main --selftest --sim
```

"Capture test template" (or `--enrol-test`) stores one embedding in the local SQLite
cache so a laptop can see the full detect → align → embed → match loop without the
server. The device never writes a face image to disk; only embeddings are stored.

## Enrolment (M4)

Two paths create templates, both gated by consent and both producing the same
112×112 crops and embeddings:

- **Bulk import**: `python tools/enroll_bulk.py --photos idcards/ --consents consents.csv`.
  Photos are `<USN>.jpg`; the CSV has `usn,consent_at,consent_version,method`
  (paper or bulk_csv). USNs without a consent row are refused before the photo is
  opened. The report CSV lists accepted/rejected files with reasons (no face, several
  faces, too small, turned away, blurry, unknown USN, no consent).
- **On the device**: tap **Enrol**, enter a teacher PIN (verified offline against the
  synced argon2 hashes), pick the student from the roster with the number pad, the
  student reads the consent notice and taps **I consent**, three frames that pass the
  gates are captured, the aligned crops are uploaded, dropped from memory, and the
  templates are synced back so the student is recognised immediately.

`python -m device.app.main --sync` pulls the catalog (courses, sections, periods,
faculty PIN hashes, consent text) and the templates for all assigned sections into the
device cache; `--full-sync` replaces the cache. Templates are synced incrementally with
deletions (consent withdrawal) and a 60 s overlap so nothing is lost around a commit.

`python tools/reembed.py --new-model-version <name>` regenerates every template from
the stored crops when the recogniser changes.
