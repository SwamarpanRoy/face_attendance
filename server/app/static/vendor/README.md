# Vendored front-end assets

No CDN at runtime: the office computer may be on a network without internet access.

| File | Project | Version | Licence | Source |
|---|---|---|---|---|
| `fonts/InterVariable.woff2` | Inter | 4.1 | SIL OFL 1.1 (`fonts/Inter-LICENSE.txt`) | https://github.com/rsms/inter/releases/tag/v4.1 (`web/InterVariable.woff2`) |
| `htmx.min.js` | htmx | 2.0.11 | Zero-Clause BSD (`htmx.LICENSE`) | https://cdn.jsdelivr.net/npm/htmx.org@2.0.11/dist/htmx.min.js |

Icons are Lucide (ISC, `lucide.LICENSE`), inlined as SVG by `server/app/admin/icons.py`
(https://github.com/lucide-icons/lucide). The styles are our own: `../app.css` (no CSS framework).

To upgrade, download the new file from the same path with the new version, update this
table, and run the admin UI tests.
