# Device API (`/api/v1`)

Interactive docs: `http://<server>:8000/docs` (OpenAPI, generated from the code).
Wire models live in `common/schemas.py` and are shared with the device app.

## Authentication

Every request carries the device token:

```
Authorization: Bearer <token>
```

Tokens are 32 random bytes, shown once when the device is registered and stored only
as a SHA-256 hash. Responses:

| Status | Meaning | Device action |
|---|---|---|
| 401 `Unknown device token.` | token not recognised | show "Device not registered" in Settings |
| 401 `Device revoked.` (+ header `X-Device-Status: revoked`) | device was revoked by an admin | **wipe the local cache** (templates, catalog, PIN hashes) and stop syncing |

Everything a device receives is scoped to the sections assigned to it in the admin UI
(Devices, assign sections).

## Endpoints

### `POST /devices/heartbeat`

Every 30 s. Body (`HeartbeatIn`):

```json
{
  "device_id": "pi-01", "app_version": "0.1.0", "ip": "10.1.2.3", "ssid": "BMSCE-WiFi",
  "queue_len": 0, "cpu_temp_c": 48.2, "free_mem_mb": 900,
  "model_version": "buffalo_s/w600k_mbf", "clock_synced": true
}
```

Response (`HeartbeatOut`): `device_name`, `server_time`, `server_version`,
`model_version` (what the server expects), `assigned_section_ids`.

### `GET /catalog`

Everything needed to start a session offline (`CatalogOut`): `courses`, `sections`
(assigned only), `periods`, `offerings` (course × section × faculty) and `faculty`
with `pin_hash` (argon2id) for faculty teaching those sections plus all admins.
Devices cache this and re-fetch it on the prefetch cycle (every 10 min).

### `POST /auth/pin`

Online PIN check: `{"pin": "246801"}` → `{"faculty_id": 7, "name": "...", "role": "faculty"}`
or 401. Devices normally verify PINs offline against the cached hashes; this exists
for the simulator and for diagnostics.

### `GET /roster?section_id=`

Students of one assigned section (`RosterOut`) for the on-device enrolment picker:
`student_id`, `usn`, `name`, `has_consent`, `template_count`. 403 for a section not
assigned to the device.

### `POST /enrolment/{usn}/captures`

On-device enrolment. Body (`EnrolmentCapturesIn`):

```json
{
  "faculty_id": 7,
  "consent": {"consent_at": "2026-10-06T10:12:00+05:30", "consent_version": "2026-10-v1", "method": "device"},
  "crops_png_b64": ["<base64 PNG 112x112>", "...", "..."],
  "blur_scores": [412.0, 380.5, 455.1]
}
```

`consent` is what the student just tapped. It may be omitted only when an active
consent already exists; otherwise the server answers **409** and stores nothing. The
server validates each crop (112×112, decodable), embeds them, writes the crops under
`data/crops/<USN>/` and creates templates with `source=device` under that consent.
Other errors: 404 unknown USN, 403 student not in an assigned section, 422 bad crop,
503 models not installed on the server.

Response (`EnrolmentCapturesOut`): `student_id`, `usn`, `consent_id`, `templates_added`,
`total_templates`, `model_version`.

The device keeps the crops in memory only and discards them after this call.

### `GET /templates?since=`

Everything the device caches for its assigned sections (`TemplatesOut`):

- `full`: true when `since` was omitted; the device then replaces its whole cache.
- `students[]`: `usn`, `name`, `section_id`, `templates[]` (`idx`, `embedding_b64` =
  512 float32 little-endian, `model_version`, `created_at`). With `since`, only
  students whose templates changed after that time, each with *all* their templates.
- `deleted_usns[]`: students whose templates were removed after `since` (consent
  withdrawal, re-enrolment); the device drops them.
- `calibrated`, `threshold`, `margin`: the active calibration for `model_version`, or
  nulls while uncalibrated (device falls back to `device.toml` and shows a warning).

Use the returned `generated_at` as the next `since`.

### `POST /probes/{usn}`

Held-out calibration crops from `tools/capture_probes.py --upload` on a Pi
(`ProbesIn`: `crops_png_b64`, 1 to 10 aligned 112×112 PNGs). Stored under
`data/probes/<USN>/`, never turned into templates. 409 without an active consent, 403
for a section not assigned to the device, 422 for an undecodable crop.
Response: `{"usn": ..., "stored": n}`.

### `POST /sessions`

Create (or re-post) a session the device minted offline (`SessionIn`): `id` (UUID made
on the device), `course_id`, `section_id`, `faculty_id`, `period_id` (nullable),
`started_at`, `clock_synced`. Idempotent: the same id returns the existing session.
Errors: 403 section not assigned to the device or session owned by another device,
404 unknown course/section, 400 course not taught to that section / inactive faculty.
Response: `{"id": ..., "status": "live"}`.

### `POST /sessions/{id}/end`

Body `{"ended_at": ..., "clock_synced": true}`. Marks the session ended and
**materialises an `absent` row for every active roster student without a record**.
Idempotent. Response (`SessionEndOut`): `present`, `absent_marked`.

### `POST /attendance/batch`

Up to 200 outbox events (`AttendanceEventIn`: `event_uuid`, `session_id`, `usn`,
`score`, `captured_at`, `clock_synced`). The response sorts every event into
`accepted`, `duplicates` (event already stored, or the student already has a record
in that session) or `rejected` (with a reason: unknown session, unknown student,
student not in the session's section). Re-sending the same batch after a lost
response is safe: everything comes back as a duplicate and no row changes. A face
event that arrives after the session ended upgrades the materialised absent row to
present. The server stamps `received_at`; when the device clock was not NTP-synced
the event carries `clock_synced=false` so reports can flag it.

Devices upload in dependency order: sessions, then event batches, then session ends.

## Trying it with REST Client

Create `scratch.http` (git-ignored pattern `*.local.*` not needed; just do not commit tokens):

```
@token = paste-device-token
POST {{baseUrl}}/api/v1/devices/heartbeat
Authorization: Bearer {{token}}
Content-Type: application/json

{"device_id": "pi-01", "app_version": "0.1.0", "queue_len": 0}
```

Select the `local` environment (`.vscode/settings.json`) and press "Send Request".
