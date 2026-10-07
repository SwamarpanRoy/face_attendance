> **Note (2026-10):** this is the original project plan. The hardware has since changed to a
> Raspberry Pi 5 (16 GB) with the Raspberry Pi AI Camera on Raspberry Pi OS Trixie; see
> `README.md` and `docs/setup_pi.md` for the current setup. Pi 4 / Camera Module 3 / BOM
> figures below describe the original plan.

# Portable Face-Recognition Attendance Device

**Final Year Project, B.E. Electronics & Communication Engineering, BMS College of Engineering, Bangalore**

This is a smartphone-sized, battery-portable device that marks class attendance by face. A teacher starts a session and each student looks at the camera. The device matches their face to their USN, shows **"Face matched"**, and records them present. A web dashboard on the office computer lets faculty monitor attendance live, correct records with a full audit trail, and export reports.

---

## Contents

1. [Problem and objectives](#1-problem-and-objectives)
2. [Scope](#2-scope)
3. [System overview](#3-system-overview)
4. [Hardware](#4-hardware)
5. [Software stack](#5-software-stack)
6. [Repository layout](#6-repository-layout)
7. [Project roadmap: start to finish](#7-project-roadmap-start-to-finish)
8. [Targets and how we measure them](#8-targets-and-how-we-measure-them)
9. [Privacy and data handling](#9-privacy-and-data-handling)
10. [Team roles](#10-team-roles)
11. [Risks and mitigations](#11-risks-and-mitigations)
12. [Future work](#12-future-work)
13. [References](#13-references)

---

## 1. Problem and objectives

Roll-call attendance wastes 5–10 minutes of every class, is easy to proxy, and leaves records scattered across registers. Fixed biometric terminals don't fit classrooms either: they create queues at the door and can't move between rooms.

**Objectives**

1. Build a **portable** embedded device that identifies a student by face and marks attendance in **under 2 seconds**.
2. Support a college-scale gallery (designed for **~2,000 students × 3 templates**). The demo uses 10–20 enrolled students.
3. Keep working through WiFi drops (**offline-first**) without losing or duplicating records.
4. Give faculty a **web dashboard** to monitor attendance live, edit any record with a mandatory reason, and export reports, including a shortage list below 75%.
5. Handle biometric data responsibly under India's **DPDP Act**:
   - explicit consent before enrolment
   - no raw images on the device
   - the ability to delete a student's biometric data on request
6. Make the whole system developable and maintainable **wirelessly**, over SSH from VS Code.

---

## 2. Scope

### In scope (this build)

| Area | Included |
|---|---|
| Edge device | Raspberry Pi 4 (2 GB), Camera Module 3, 5" Touch Display 2, portrait touch UI, offline queue, sync, heartbeat |
| Recognition | InsightFace `buffalo_s`: det_500m detector + ArcFace w600k_mbf recogniser, run on CPU via ONNX Runtime; quality gates; threshold + margin + 2-of-3 voting |
| Enrolment | (a) bulk import from existing ID-card photos, (b) three-image capture on the device; consent timestamp is mandatory |
| Server | FastAPI + PostgreSQL on the office computer; device API; template distribution; idempotent attendance ingest |
| Dashboard | Login with roles (admin/faculty), live session view, per-student history, **manual edit with reason + audit log**, reports, CSV/XLSX export, device monitoring |
| Calibration | Threshold chosen from genuine/impostor score distributions (`calibrate.py`), with FAR/FRR reported |
| Wireless dev/ops | Key-only SSH, VS Code Remote-SSH, one-click deploy, remote log tailing, remote debugger, Tailscale fallback, remote screen view |

### Out of scope (deferred, with hooks left in the code)

- **Anti-spoofing / IR liveness hardware.** A `LivenessCheck` interface exists with a no-op implementation. The prototype *can* be fooled by a photo, and we state this openly in the report.
- Cancelable / protected templates.
- Integration with the college ERP.
- A mobile app (the dashboard is responsive and works in a phone browser instead).
- A commercial product: this is an academic prototype.

> **Model licence note:** InsightFace's pretrained models are released for non-commercial research use. That fits an academic project, but it would need revisiting for any commercial use.

---

## 3. System overview

```
             ┌──────────────── EDGE DEVICE (Pi 4, 2 GB) ────────────────┐
 Student ──► │ Camera Module 3 → detect (det_500m) → quality gates       │
             │ → align 112×112 → embed (w600k_mbf, 512-d)                │
             │ → cosine match vs section templates → vote → "Matched!"   │
             │ PyQt5 touch UI · SQLite template cache + offline outbox   │
             └───────────────┬──────────────────────────────────────────┘
                             │ campus WiFi (or Tailscale), device token
             ┌───────────────▼──────── SERVER (office computer) ────────┐
             │ FastAPI device API · PostgreSQL · enrolment crops on disk │
             │ Admin dashboard (Jinja2 + HTMX): live view, edits, audit, │
             │ reports, exports, device health                           │
             └───────────────▲──────────────────────────────────────────┘
                             │ browser (laptop / phone)
                        Faculty / Admin
```

**Attendance flow**

1. The teacher taps *Start session*, picks the course, section and period, and enters a PIN.
2. The device loads that section's templates. It refreshes them from the server if it's online.
3. A student faces the camera. The gates check for one face, enough size, sharpness and a frontal pose.
4. The device embeds the face and matches it. It accepts only if all three hold:
   - the score is above the threshold
   - the score beats the second-best student by the margin
   - the same student won 2 of the last 3 frames
5. The device shows "Face matched – Name (USN)". The record goes into the local outbox and syncs to the server.
6. The dashboard updates live. At the end of the session, students without a record are marked absent.

---

## 4. Hardware

| Part | Choice | Notes |
|---|---|---|
| Compute | Raspberry Pi 4 Model B **2 GB** (~₹7,091) | Chosen over the Pi 5 4 GB to halve the board cost; ESP32-CAM + server and Pi Zero 2 W were ruled out |
| Camera | Raspberry Pi Camera Module 3 | Autofocus helps at varying distances |
| Display | Official Raspberry Pi Touch Display 2, 5" (720×1280, DSI, capacitive) (~₹5,379) | Portrait UI; chosen over the Waveshare 4.3" DSI, a 5" HDMI resistive panel, and the no-screen option |
| Storage | microSD (A1/A2-rated, 32 GB+) | Keep a spare card that is already flashed |
| Cooling | Heatsink (+ small fan if the enclosure is closed) | Prevents thermal throttling during long sessions |
| Power | **To decide:** USB-C power bank (5 V / 3 A) or a UPS/battery HAT | Measure the runtime of one full session day |
| Enclosure | **To decide:** 3D-printed / acrylic case, handheld or on a desk stand | The camera should sit roughly at face height when in use |
| Server | Existing office computer | Runs the FastAPI server + PostgreSQL |

**BOM total in the synopsis deck:** ~₹17,380 (Pi 4 2 GB build). See the parts list and market survey for the per-item breakdown and sources (a mix of Indian sellers and imports).

---

## 5. Software stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 (Pi OS Bookworm default) |
| Device OS | Raspberry Pi OS 64-bit (Bookworm) |
| Camera | `picamera2` / libcamera |
| Inference | ONNX Runtime (CPU), InsightFace `buffalo_s` models |
| Device UI | PyQt5 (from apt), portrait touch screens |
| Device storage | SQLite (template cache, sessions, outbox) |
| Server | FastAPI, SQLAlchemy 2.x, Alembic, Uvicorn |
| Database | PostgreSQL 16 |
| Dashboard | Jinja2 + HTMX (no JS build), responsive |
| Reports | CSV, XLSX (openpyxl) |
| Dev tooling | VS Code, Remote-SSH, debugpy, ruff, pytest, mypy, Git/GitHub |
| Networking | Campus WiFi; Tailscale as a fallback; Raspberry Pi Connect or VNC for remote screen viewing |

---

## 6. Repository layout

```
face-attendance/
├── common/face/        detector, align, embedder, quality, matcher, liveness (shared)
├── device/app/         Pi app: camera, pipeline, UI screens, SQLite store, sync
├── server/app/         FastAPI: device API, admin dashboard, services, templates
├── server/alembic/     DB migrations
├── tools/              fetch_models, enroll_bulk, calibrate, reembed, bench_pi, deploy, seed_demo
├── scripts/            setup_pi.sh, setup_server.sh / .ps1
├── docs/               setup_pi, wireless_dev, api, operations
├── tests/              common/, device/, server/
├── .vscode/            tasks, launch (incl. remote debug), recommended extensions
├── models/             (git-ignored) ONNX files
└── data/               (git-ignored) enrolment crops, backups
```

---

## 7. Project roadmap: start to finish

Each phase lists its goal, tasks, deliverable and "done when" check. The phases roughly follow the milestones in `PROMPT.md` (M0–M9). Adjust the week numbers to match your Project Work-2 review dates.

### Phase 0: Planning and accounts (Week 1)

**Goal:** everyone can contribute from day one.

- [ ] Create the GitHub repo and add all four members and the guide (read access).
- [ ] Agree on branch rules: `main` is protected; feature branches merge via pull request.
- [ ] Every member generates an SSH key (`ssh-keygen -t ed25519`) and shares their **public** key.
- [ ] Create a free Tailscale account (the fallback for campus WiFi isolation).
- [ ] Install VS Code with the recommended extensions on every laptop.
- [ ] Freeze the scope (Section 2) and get the guide's sign-off.

**Deliverable:** repo, access, agreed scope.

### Phase 1: Procurement (Weeks 1–2, in parallel)

- [ ] Order the Pi 4 2 GB, Camera Module 3, Touch Display 2, microSD, official power supply (for the bench) and heatsink.
- [ ] Decide on and order the portable power option. Check the delivery time on imported parts.
- [ ] Keep invoices for the project report's cost section.

**Done when:** all parts are on the bench and the Pi boots with the display and camera attached.

### Phase 2: Pi setup and wireless development (Week 2)

**Goal:** never plug a keyboard or monitor into the Pi again.

1. Flash **Raspberry Pi OS 64-bit** with Raspberry Pi Imager. In the customisation settings:
   - set the hostname and user
   - add WiFi
   - **enable SSH with public-key only**, pasting your public key
2. Boot, then `ssh <user>@<hostname>.local` from the laptop.
3. Add a host entry to `~/.ssh/config` so `ssh attendance-pi` just works.
4. **If the campus WiFi blocks device-to-device traffic** (common), install Tailscale on the Pi, the laptops and the office computer, and use the Tailscale names. A phone hotspot works as a quick backup.
5. For WPA2-Enterprise campus WiFi, configure it with `nmcli` (see `docs/setup_pi.md`).
6. Run `scripts/setup_pi.sh`. It installs:
   - camera, Qt and numpy from apt
   - the venv (with system packages)
   - autostart
   - SSH hardening
   - a passwordless restart rule
7. Enable Raspberry Pi Connect or VNC to view the touch screen remotely.
8. In VS Code, check that each of these works:
   - **Pi: deploy**
   - **Pi: tail logs**
   - **Pi: restart app**
   - **Attach to Pi** (debugger)

**Done when:** a hello-world app autostarts on the touch screen, and you can change it, deploy it and debug it entirely over WiFi.

### Phase 3: Repository and laptop environment (Week 2)

- [ ] Scaffold the repo (M0): pyproject, ruff, pytest, `.vscode/`, `.gitignore`, `.env.example`.
- [ ] `tools/fetch_models.py` downloads and verifies the `buffalo_s` models.
- [ ] A USB webcam works in **simulator mode** on every laptop, so recognition work doesn't wait for the Pi.

**Done when:** `Tests: all` passes on every member's laptop.

### Phase 4: Server and database (Weeks 3–4)

- [ ] Install PostgreSQL 16 on the office computer (Docker Compose or native).
- [ ] Create the schema with Alembic migrations:
  - students, consents, face templates, sections, courses, faculty, devices, sessions, attendance, audit log
- [ ] Add database rules: unique `(session, student)`, `consent_at NOT NULL`, and a mandatory reason on edits.
- [ ] Build the admin login with argon2 password hashing, roles (admin/faculty) and CSRF protection.
- [ ] Build the device API:
  - heartbeat, catalog, PIN verify, templates, sessions, batch attendance (idempotent), roster, enrolment captures
- [ ] Write `seed_demo.py` for demo courses, sections, faculty and placeholder students.

**Done when:** migrations apply on a fresh database, the API's `/docs` page works, and API tests pass.

### Phase 5: Recognition core (Weeks 3–5)

- [ ] **Detector wrapper:** preprocessing, anchor decoding, NMS, 5 landmarks.
- [ ] **Alignment:** 5-point similarity transform to the ArcFace 112×112 template.
- [ ] **Embedder:** a 512-d, L2-normalised vector.
- [ ] **Quality gates:** single face, minimum size, blur (Laplacian variance), yaw from landmarks.
- [ ] **Matcher:** vectorised cosine similarity, max per student, margin over the runner-up, 2-of-3 vote, per-session cooldown.
- [ ] Unit tests on fixed fixtures.

**Done when:** in simulator mode, a teammate is recognised against their own template and rejected against others.

### Phase 6: Enrolment (Weeks 5–6)

- [ ] **Consent process:** write a short consent notice and fix a version number for it. Collect consent either on the device ("I consent" button) or on paper, then record it as a CSV.
- [ ] **Bulk import** (`enroll_bulk.py`): ID-card photos named by USN, plus the consent CSV.
  - It rejects any USN without consent.
  - It quality-checks each photo and writes a rejection report.
- [ ] **On-device enrolment:**
  1. Pick the student from the roster.
  2. The student gives consent.
  3. Capture 3 images.
  4. Upload the crops. Nothing is kept on the device.
- [ ] Store crops at `data/crops/<USN>/` on the server only.
- [ ] Write `reembed.py` to regenerate templates from the crops if the model changes.

**Done when:** 10–20 demo students are enrolled, and an enrolment attempt without consent is refused.

### Phase 7: Calibration (Week 6)

- [ ] Capture 3–5 held-out **probe images** per enrolled student with `capture_probes.py`, ideally on a different day or under different lighting. These are test images only and never become templates.
- [ ] Run `calibrate.py` (written from scratch in M5). It scores the probes against the templates with the same matcher code the device uses, giving genuine and impostor score distributions.
- [ ] Choose a threshold at the target false-accept rate, set the margin, and note the EER.
- [ ] Save the histogram, FAR/FRR and DET plots and the results table. These go straight into the report. State the number of trials honestly, since a small demo set limits how precise the FAR can be.
- [ ] Activate the calibration on the server; devices pick it up on their next sync. Recalibrate after any model or enrolment change.

**Done when:** the threshold is justified by data, not guessed.

### Phase 8: Device application (Weeks 6–8)

- [ ] Build the screens:
  - **Idle:** server status, queue count, WiFi/IP
  - **Start session:** course → section → period, then PIN
  - **Live:** camera preview, banner, counter, last 5 marked
  - **Enrol**
  - **Settings**
- [ ] Run the pipeline on a worker thread so the UI never freezes.
- [ ] Build offline-first storage:
  - **prefetch templates for every section assigned to the device**, plus the catalog, faculty PIN hashes and the calibrated threshold, at boot and every 10 minutes while online
  - PINs verified on the device, so a session can start with no network
  - "Last synced" time shown on the Idle screen
  - SQLite outbox with a UUID per record
  - sync every 5 s with backoff
  - heartbeat every 30 s
- [ ] Handle the clock: the Pi 4 has no RTC, so warn if NTP isn't synced and flag the affected records.
- [ ] Optionally add a sound or spoken "Face matched".

**Done when:** with WiFi off from boot, a session can be started and students recognised; pulling the WiFi mid-session loses nothing; and after reconnecting, records sync **without duplicates**.

### Phase 9: Admin dashboard (Weeks 7–9)

- [ ] **Dashboard:** today's sessions, device status, students below the attendance threshold.
- [ ] **Live session view:** roster grid that auto-refreshes every 3 s, showing time and match score.
- [ ] **Edit attendance:**
  - change to present / absent / late / excused
  - **a reason is mandatory**
  - before/after is saved to the audit log
  - bulk edit (for example, "college event – excused")
- [ ] **Student page:**
  - consent record, number of templates, per-course % and history
  - re-enrol
  - **withdraw consent and delete biometric data**
- [ ] **Reports:** date ranges, per-course %, 75% shortage list, CSV/XLSX export.
- [ ] **Devices page:** register (token shown once), revoke, last heartbeat, temperature, queue length.
- [ ] **Audit log viewer.**

**Done when:** a faculty member can watch a live session from a phone browser and correct a record. Admins can see who changed what, when, and why.

### Phase 10: Pi port and performance (Weeks 9–10)

- [ ] Run the full app on the Pi. Run `bench_pi.py` to get p50/p95 for each stage and end-to-end.
- [ ] Tune the detector input size, thread count and preview resolution until **p95 is under 2 s**.
- [ ] Check for thermal throttling (`vcgencmd get_throttled`) during a 30-minute run. Add a fan if needed.
- [ ] Check autostart, crash restart, and recovery after a reboot or WiFi loss.

**Done when:** the benchmark table meets the target and the device survives a power cycle unattended.

### Phase 11: Integration testing (Weeks 10–11)

- [ ] End-to-end test: simulator device → server → dashboard.
- [ ] Classroom-style trial with all demo students:
  - record recognition time per student
  - record false accepts and false rejects
  - test under different lighting (tube light, window light, evening)
- [ ] **Failure drills:**
  - WiFi off mid-session
  - server down
  - Pi reboot mid-session
  - wrong PIN
  - two faces in frame
  - an unenrolled person
- [ ] Security pass:
  - key-only SSH
  - no secrets in git
  - device tokens hashed
  - faculty can't edit other courses

**Deliverable:** a test report with tables and photos for the project report.

### Phase 12: Enclosure, power and finishing (Weeks 10–12)

- [ ] Design and build the enclosure (camera window, display cut-out, ventilation, access to the SD card and power).
- [ ] Fit the battery solution. Measure the runtime and charge time.
- [ ] Label the device, and add a quick-start card for faculty.

**Done when:** the device runs a full session day on battery and is comfortable to hold or stand on a desk.

### Phase 13: Demo, documentation and report (Weeks 12–14)

- [ ] Write a demo script, rehearsed with the team:
  1. Cold boot.
  2. Start a session.
  3. Mark 10 students.
  4. Disconnect the WiFi, keep marking, reconnect, and show the sync.
  5. Edit a record on the dashboard with a reason, and show the audit entry.
  6. Export the report.
- [ ] Keep a backup demo path ready: a pre-recorded video plus simulator mode on a laptop.
- [ ] Final docs: `docs/setup_pi.md`, `docs/wireless_dev.md`, `docs/api.md`, `docs/operations.md` (backups, restore, troubleshooting).
- [ ] Final project report in the BMSCE format, including:
  - architecture
  - BOM and cost comparison
  - calibration plots
  - benchmark tables
  - test results
  - privacy design
  - limitations (no liveness)
  - future work
- [ ] Make sure the slides and report use **the same figures**, since earlier submissions used older BOM numbers.

**Done when:** a clean end-to-end demo works from a cold boot, and a new person could set up the system from the README and docs alone.

---

## 8. Targets and how we measure them

| Target | Value | Measured by |
|---|---|---|
| Recognition latency (Pi 4) | p95 < 2 s, face-in-frame to "Matched" | `tools/bench_pi.py`, plus a stopwatch in the classroom trial |
| Accuracy | Operating point chosen at the target FAR; report TAR/FRR | `tools/calibrate.py` plots and table |
| Throughput | Report students marked per minute | Classroom trial |
| Offline resilience | 0 lost, 0 duplicated records | WiFi-off drill |
| Boot to ready | ≈ 60 s | Cold-boot timing |
| Battery | ≥ one teaching day (to be measured) | Runtime test |
| Thermal | No throttling in a 30-min session | `vcgencmd get_throttled` |

---

## 9. Privacy and data handling

- **Consent first.** No enrolment without a consent timestamp. This is enforced by the database, the API and the UI. Every consent record stores its version and method.
- **Data minimisation on the device.** The device stores only embeddings for the active sections. Raw images are never written to its disk.
- **Crops stay on the office computer**, at `data/crops/<USN>/`. They exist only so templates can be regenerated, and they are covered by backups and access control.
- **Templates are sensitive too.** Research (Mai et al.) shows faces can be partially reconstructed from deep templates. Treat embeddings like personal data: access-controlled, never logged, deleted on withdrawal.
- **Right to erasure.** "Withdraw consent" deletes the student's templates and crops. The attendance history is kept, and the deletion is audited.
- **Every manual change is audited**, recording who, when, before, after and why.
- **Fairness.** Report results across the demo group honestly, and note the demographic-effect findings (NISTIR 8280) as a known limitation of any face-recognition system.

---

## 10. Team roles

The member names are placeholders; fill them in to match the cover page.

| Member | Primary ownership | Also supports |
|---|---|---|
| Member 1 | Pi platform: OS setup, wireless dev tooling, camera/display, enclosure, power, benchmarking | Phase 10 tuning |
| Member 2 | Recognition core: detector, alignment, embedder, matcher, quality gates, calibration | Enrolment tools |
| Member 3 | Server: database, device API, admin dashboard, reports, auth | Sync protocol |
| Member 4 | Device app UI + sync, integration testing, docs, project report and slides | Demo script |

**Weekly rhythm:** a short sync meeting; update the checklist in this README; demo progress to the guide every two weeks.

---

## 11. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Campus WiFi blocks device-to-device traffic or needs enterprise login | Can't SSH; device can't reach server | Tailscale on all machines; `nmcli` enterprise config; phone-hotspot fallback |
| Pi 4 too slow for < 2 s | Misses the key target | Smaller detector input, recognise only after gates pass, 4 ONNX threads, benchmark early (Phase 10 can begin as soon as Phase 5 is done) |
| apt vs pip numpy conflict with picamera2 | Camera app crashes | venv with `--system-site-packages`; never pip-install numpy/opencv on the Pi |
| Thermal throttling in an enclosure | Latency spikes | Heatsink/fan, vents, monitor with `vcgencmd` |
| SD-card corruption on sudden power loss | Device won't boot | Good-quality card, clean shutdown option in Settings, a spare pre-flashed card |
| Pi 4 has no real-time clock | Wrong timestamps offline | NTP check, `clock_synced` flag, server `received_at` |
| Old or low-quality ID-card photos | Poor matches | Quality-check report; re-enrol on the device for rejected students |
| Lighting variation | False rejects | Test under several lighting conditions; adjust exposure; position the device away from backlight |
| Photo spoofing (no liveness yet) | Proxy attendance possible | State it as a known limitation; `LivenessCheck` hook; teacher supervises; IR liveness as future work |
| Wrong or forgotten manual edits | Disputes | Mandatory reason + audit log + role restrictions + edit window |
| Demo-day failure | Bad evaluation | Rehearsed script, spare SD card, simulator and recorded-video backup |

---

## 12. Future work

- IR / depth-based **liveness detection** (passive anti-spoofing first, then IR hardware).
- **Cancelable templates** for stronger biometric template protection.
- Quality-aware recognition (AdaFace-style) and face-image quality scoring (SER-FIQ) to improve enrolment acceptance.
- Several devices per department, with timetable-driven automatic sessions.
- Integration with the college ERP / LMS.
- Hardware acceleration (NPU/TPU add-on) for larger galleries.

---

## 13. References

The project's literature base is limited to the following peer-reviewed sources and standards (full citations are in the synopsis report):

- ArcFace: additive angular margin loss for deep face recognition
- RetinaFace: single-stage dense face localisation
- MobileFaceNets: efficient face verification on mobile devices
- AdaFace: quality-adaptive margin for face recognition
- SER-FIQ: unsupervised face image quality estimation
- Face anti-spoofing survey (IEEE TPAMI)
- NISTIR 8280: demographic effects in face recognition
- NISTIR 8429
- Mai et al.: reconstructing face images from deep templates
- Cancelable biometric templates (EURASIP)

---

*Status tracking:* tick the checkboxes in Section 7 as you go, so this README doubles as the project's progress tracker.
