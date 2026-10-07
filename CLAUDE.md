# Working agreement for AI coding agents in this repo

The full build spec lives with the team; this file restates what must survive across
sessions. Read it before changing anything.

## Status

All milestones that can be built without hardware are done (M0, M2 to M7, M9). The
Pi-side pieces of M1 and M8 are written but unverified: run `docs/setup_pi.md`, then
`Pi: deploy`, `--selftest` and `tools/bench_pi.py` on the real Pi and fix what it reveals.
Milestone table: README.md. Per-area docs: `docs/`.

**Actual hardware (2026-10):** Raspberry Pi 5 16 GB, Raspberry Pi AI Camera (IMX500,
manual focus, on-sensor NPU unused), Raspberry Pi OS Trixie (Python 3.13). For now the
Pi is used on an HDMI monitor with mouse/keyboard (no SSH yet, no touch display yet);
the laptop is macOS. Older text that says Pi 4 2 GB / Camera Module 3 / Bookworm
describes the original plan.

## Workflow

- Work **one milestone at a time** (table in README.md). After each milestone: stop,
  summarise, list manual steps for the human, wait for a go-ahead.
- If the spec is ambiguous or conflicts with the repo, **ask instead of guessing**.
  Never invent hardware we do not have.
- **Hardware may be absent.** If the Pi is not available, skip M1 and do M2 to M7 on the
  laptop in simulator mode, then come back to M1. Until the office computer exists, the
  server and PostgreSQL run on the laptop. Hands-on steps (flashing SD cards, WiFi
  passwords, creating accounts) are the human's: give exact step-by-step instructions.
- Keep `README.md`, `docs/*.md`, `.env.example` and `device/device.example.toml`
  in sync with the code as you go.

## Hard constraints (do not change without asking)

- **Python 3.13** runs everywhere (Pi OS Trixie on the Pi, the laptop venv). Code stays
  3.11-compatible: ruff/mypy target 3.11, so do not use newer syntax in shared code.
  numpy is pinned to 2.2.4 on the laptop/server to match Trixie's apt numpy.
- **Pi packages from apt:** picamera2, libcamera, PyQt5, numpy, opencv. The venv is
  created with `--system-site-packages`. **Never add numpy or opencv to
  `device/requirements.txt`**; pip versions break picamera2's ABI.
- **Inference on the Pi = onnxruntime directly.** No `insightface` package on the
  device. `common/face/` ports only detector pre/post-processing, NMS and 5-point
  alignment.
- **Models are never committed.** `tools/fetch_models.py` downloads the official
  release zip, verifies pinned SHA-256 hashes (zip and each model) and writes
  `models/det_500m.onnx` and `models/w600k_mbf.onnx`. Those two files are identical in
  `buffalo_s` and `buffalo_sc`.
- **Thresholds come from measured data, never a hard-coded guess.**
  `tools/capture_probes.py` collects held-out probes; `tools/calibrate.py` (written
  from scratch) scores them with the *same* `common/face/matcher.py` the device uses,
  picks threshold at the target FAR and margin at a genuine-gap percentile, and stores
  an active `calibrations` row that devices receive on sync. Until then the device uses
  a clearly labelled provisional fallback from `device.toml` and shows "not calibrated"
  on the Settings screen.
- **Offline-first device.** At boot and every 10 minutes online it prefetches templates
  for *all* sections assigned to it, the catalog, faculty PIN hashes (argon2, verified on
  the device) and the active calibration, incrementally (`since=`) including deletions.
  Sessions and events are created on the device with UUIDs; ingestion is idempotent.
- **PINs unlock device actions only** (start/end session, enrol, settings). They must
  never work as dashboard passwords. A revoked device wipes its cache on next contact.
- **Privacy (DPDP):** the device never stores raw face images (embeddings only;
  enrolment crops go to the server and are dropped from memory after upload). Crops
  and probes live only under `data/` on the server. **Never log embeddings or
  images.** **No enrolment without consent**: enforced at DB (`NOT NULL`), API and UI.
  The consent notice must say that held-out test images are taken.
- **Server:** FastAPI, PostgreSQL 16, SQLAlchemy 2.x, Alembic. Admin UI is
  server-rendered Jinja2 + HTMX, vendored static files, no JS build step.
- **Device UI:** PyQt5 (apt on the Pi), portrait 720x1280, touch-first, big buttons.
  No browser kiosk, no heavy frameworks (keep it lean even though the Pi 5 has 16 GB).
  Until the touch display arrives it runs in a window on an HDMI monitor; mouse = touch.
- **Performance budget:** under 2 s from face-in-frame to "Face matched" on the
  Pi 5 CPU (measure p50/p95 with `tools/bench_pi.py`).
- Anti-spoofing is out of scope: keep the `LivenessCheck` interface + `NoOpLiveness`.

## Coding standards

- Small typed modules; docstrings explain *why*. `ruff` (D and ANN rules on) and
  `mypy --strict` on `common/` and `server/` must pass.
- No global mutable state. Dependency-inject the camera source, the clock and the
  HTTP client.
- All thresholds and sizes live in config (`.env` / `device.toml`), never hard-coded.
- Pin dependency versions in the requirements files.
- Device-facing error text is a short plain-English instruction ("Move closer").

## Commands (laptop, from repo root, venv active)

```
python -m pytest                                    # fast unit tests (VS Code task: Tests: all)
python -m pytest --run-integration -m integration   # needs TEST_DATABASE_URL
ruff check . && ruff format --check .
mypy                                                # common/ + server/
python tools/fetch_models.py                        # models/ (git-ignored)
```
