# Demo script (10–20 students, cold boot to corrected record)

Rehearse this twice. Total time about 15 minutes.

## Day before

1. Server up (`/healthz` ok), `tools/seed_demo.py` run, real course/section/period rows
   created, roster imported (Sections → Import CSV), course assigned to the section with
   the presenting teacher as faculty.
2. Device registered (Devices → Register), token in `device.toml`, section assigned,
   `--selftest` all PASS, `--sync` done.
3. Enrol 10–20 volunteers on the device (Enrol → teacher PIN → pick → *I consent* → 3
   captures). Each takes under a minute. Keep 2–3 volunteers un-enrolled for the "Not
   recognised" moment.
4. Capture probes for 5+ enrolled volunteers on a different day/light
   (`tools/capture_probes.py --usn … --count 4`), run `tools/calibrate.py`, open the
   report (`calibration/<ts>/report.md`, histogram, FAR/FRR). *Settings* on the device
   now shows the calibrated threshold; the "Not calibrated" warning is gone.
5. `tools/bench_pi.py` on the Pi: note p50/p95 for the slides.
6. Charge the power bank; put the phone hotspot credentials on the Pi as fallback.

## Live (what to say, what to show)

1. **Cold boot** (0:00). Plug in the Pi. Narrate the hardware (Pi 5 16 GB, Raspberry
   Pi AI Camera, 5" touch display, battery). App appears on the touch screen within ~60 s
   with the clock, server status and *Start session*.
2. **Start a session** (1:30). *Start session* → course → section → period → teacher
   PIN. Point out that this works from the local catalog even without WiFi.
3. **Mark attendance** (2:30). Volunteers walk up one at a time. Show the gate hints
   ("Move closer", "One person at a time"), then *Face matched – Name (USN)* in under
   2 s each. Second attempt by the same person → *Already marked*. An un-enrolled
   volunteer → *Not recognised – try again*. Mention: no images are stored on the
   device, only 512-number templates.
4. **Live dashboard** (5:00). On a phone: `/admin` → Sessions → the live session. The
   roster grid updates every 3 s with time and score. Show the dashboard counts.
5. **WiFi loss** (6:30). Turn off the hotspot / unplug the AP. Mark two more volunteers:
   the idle/live screen shows *server: offline* and *Queued records: 2*. Turn WiFi back
   on: within seconds the queue drains, the grid on the phone catches up, no duplicates
   (show the audit of the batch if asked: the events carry client UUIDs).
6. **End session** (8:30). *End session* → PIN. Dashboard: session ended, absentees
   filled in automatically.
7. **Correct a record** (9:30). On the phone, open a student who was marked absent but
   was present, *Edit* → *present* → reason "was presenting at the dais". Show the
   student page history and the **Audit log** entry with before/after and reason.
8. **Reports** (11:00). Reports → shortage list → export XLSX; open it.
9. **Privacy** (12:00). Student page → *Withdraw consent & delete biometric data* for
   one volunteer (with reason): templates and crops gone, attendance kept, audited; the
   device drops them on the next sync (show the Enrolment page counts change).
10. **Engineering** (13:00). Calibration report plots, bench numbers, the verification
    against the InsightFace reference, the test suite (`pytest` green), and the VS Code
    *Pi: tail logs* task running over WiFi.

## If something goes wrong

- Camera black: *Settings → Test camera*; reseat the ribbon; `rpicam-hello --list-cameras`.
- Server unreachable: continue offline (that is the point); check hotspot; the queue
  drains later.
- A face keeps failing: better light from the front, remove cap/mask, 50–80 cm distance.
- App frozen: *Settings → Restart app* or `Pi: restart app` from the laptop; the session
  can be **resumed** from the idle screen with the cooldown intact.
