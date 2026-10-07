# Admin web UI (`/admin`)

Server-rendered Jinja2 + HTMX, no JavaScript build, works on a phone browser. Sign in
with a faculty/admin account (argon2 passwords, HttpOnly session cookie, CSRF token on
every form). Device PINs never work here.

## Roles

| | admin | faculty |
|---|---|---|
| Dashboard, sessions, reports, students | everything | only courses they teach (`course_sections`) or sessions they ran |
| Edit attendance | always | own sessions, within `FACULTY_EDIT_WINDOW_DAYS` (default 7) of the session start |
| Courses / sections / periods | create and edit | view |
| Faculty, devices, enrolment overview, audit log | yes | no (403) |

## Pages

- **Dashboard**: today's sessions (live/ended, present count), devices online/offline,
  sync warnings (offline > 10 min, records waiting, clock not NTP-synced, never
  contacted), students below the shortage threshold over the last 90 days.
- **Sessions**: list by day/course/status; **live view** with a roster grid that
  refreshes every 3 s (HTMX), showing status chips, capture time and match score.
  Click **Edit** on a student to set present/absent/late/excused with a **mandatory
  reason**; **bulk edit** applies one status and reason to several students; **End
  session** from the browser if the device cannot (materialises absents). Every change
  writes an `audit_log` row with before/after, the reason, and sets `method=manual`.
- **Students**: search, create, edit; the student page shows consents, templates
  (count, source, quality), attendance percentage per course, full history,
  **Re-enrol** (drops templates, keeps consent) and **Withdraw consent & delete
  biometric data** (templates and crops deleted, attendance kept, audited; devices drop
  the student on their next sync).
- **Courses / Sections / Periods**: CRUD, course→section→faculty assignment (drives
  faculty permissions and the device picker), roster import from CSV.
- **Reports**: per-course, per-student percentage with date range, section and course
  filters; **shortage list** below a configurable threshold (default 75%); export to
  **CSV** and **XLSX**. Rule: `attended (present + late) ÷ (held − excused)` over ended
  sessions.
- **Enrolment**: counts, students missing consent, consented-but-not-enrolled, recent
  bulk imports (accepted/rejected counts and report path).
- **Devices**: register (token shown once), assign sections to cache, rotate token,
  revoke (device wipes its cache on next contact), last heartbeat details.
- **Audit log**: filter by user, entity, action prefix and date.
- **My account**: change password, set the device PIN (unique across accounts).

Times are shown in `TIMEZONE` (default `Asia/Kolkata`); the database stores UTC.
