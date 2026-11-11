"""Lucide icons (ISC licence, static/vendor/lucide.LICENSE) as inline SVG for templates.

Inline, not an icon font or sprite file: no extra request, colours follow ``currentColor``
and the dashboard keeps working offline. ``{{ icon("users") }}`` in any template.
"""

from __future__ import annotations

from markupsafe import Markup, escape

# Inner markup of each 24x24 Lucide icon (stroke-based).
_ICONS: dict[str, str] = {
    "arrow-right": ('<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>'),
    "book-open": (
        '<path d="M12 5v16"/><path d="M20.001 19A2 2 0 0022 17V5a2 2 0 00-1.999-2L16 3.002A5 '
        "5 0 0012 5a5 5 0 00-4-2H4a2 2 0 00-2 2v12a2 2 0 001.999 2H8a5 5 0 014 2 5 5 0 014-2z"
        '"/>'
    ),
    "calendar-check": (
        '<path d="M8 2v3"/><path d="M16 2v3"/><rect x="3" y="3" width="18" height="18" rx="2"'
        '/><path d="M3 9h18"/><path d="m9 15 2 2 4-4"/>'
    ),
    "chart-column": (
        '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9"/><path d="M13 17V5"/><path d='
        '"M8 17v-3"/>'
    ),
    "check": ('<path d="M20 6 9 17l-5-5"/>'),
    "chevron-right": ('<path d="m9 18 6-6-6-6"/>'),
    "circle-user": (
        '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="10" r="3"/><path d="M7 20.662V19'
        'a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v1.662"/>'
    ),
    "clock": ('<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'),
    "cpu": (
        '<path d="M12 20v2"/><path d="M12 2v2"/><path d="M17 20v2"/><path d="M17 2v2"/><path '
        'd="M2 12h2"/><path d="M2 17h2"/><path d="M2 7h2"/><path d="M20 12h2"/><path d="M20 1'
        '7h2"/><path d="M20 7h2"/><path d="M7 20v2"/><path d="M7 2v2"/><rect x="4" y="4" widt'
        'h="16" height="16" rx="2"/><rect x="8" y="8" width="8" height="8" rx="1"/>'
    ),
    "download": (
        '<path d="M12 15V3"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7'
        ' 10 5 5 5-5"/>'
    ),
    "file-clock": (
        '<path d="M16 22h2a2 2 0 0 0 2-2V8a2.4 2.4 0 0 0-.706-1.706l-3.588-3.588A2.4 2.4 0 0 '
        '0 14 2H6a2 2 0 0 0-2 2v2.85"/><path d="M14 2v5a1 1 0 0 0 1 1h5"/><path d="M8 14v2.2l'
        '1.6 1"/><circle cx="8" cy="16" r="6"/>'
    ),
    "file-spreadsheet": (
        '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2'
        '.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"/><path d="M14 2v5a1 1 0 0 0 1 1h5"/><path d="M8'
        ' 13h2"/><path d="M14 13h2"/><path d="M8 17h2"/><path d="M14 17h2"/>'
    ),
    "info": ('<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>'),
    "layers": (
        '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 '
        '1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z"/><path d="M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0'
        ' 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12"/><path d="M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 '
        '0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17"/>'
    ),
    "layout-dashboard": (
        '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="'
        '3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="'
        '5" x="3" y="16" rx="1"/>'
    ),
    "list-checks": (
        '<path d="M13 5h8"/><path d="M13 12h8"/><path d="M13 19h8"/><path d="m3 17 2 2 4-4"/>'
        '<path d="m3 7 2 2 4-4"/>'
    ),
    "log-out": (
        '<path d="m16 17 5-5-5-5"/><path d="M21 12H9"/><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0'
        ' 0 1 2-2h4"/>'
    ),
    "menu": ('<path d="M4 5h16"/><path d="M4 12h16"/><path d="M4 19h16"/>'),
    "monitor-smartphone": (
        '<path d="M18 8V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h8"/><path d="M10 19v-'
        '3.96 3.15"/><path d="M7 19h5"/><rect width="6" height="10" x="16" y="12" rx="2"/>'
    ),
    "pencil": (
        '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.3'
        '52a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>'
    ),
    "plus": ('<path d="M5 12h14"/><path d="M12 5v14"/>'),
    "radio": (
        '<path d="M16.247 7.761a6 6 0 0 1 0 8.478"/><path d="M19.075 4.933a10 10 0 0 1 0 14.1'
        '34"/><path d="M4.925 19.067a10 10 0 0 1 0-14.134"/><path d="M7.753 16.239a6 6 0 0 1 '
        '0-8.478"/><circle cx="12" cy="12" r="2"/>'
    ),
    "scan-face": (
        '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17'
        'v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M8 14s1.5 2 4 2 4'
        '-2 4-2"/><path d="M9 9h.01"/><path d="M15 9h.01"/>'
    ),
    "scroll-text": (
        '<path d="M15 12h-5"/><path d="M15 8h-5"/><path d="M19 17V5a2 2 0 0 0-2-2H4"/><path d'
        '="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1'
        ' 0-4 0v2a1 1 0 0 0 1 1h3"/>'
    ),
    "search": ('<path d="m21 21-4.34-4.34"/><circle cx="11" cy="11" r="8"/>'),
    "shield-check": (
        '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0'
        " 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1"
        ' 1z"/><path d="m9 12 2 2 4-4"/>'
    ),
    "thermometer": ('<path d="M14 4v10.54a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0Z"/>'),
    "upload": (
        '<path d="M12 3v12"/><path d="m17 8-5-5-5 5"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0'
        ' 0 1-2-2v-4"/>'
    ),
    "user-cog": (
        '<path d="M10 15H6a4 4 0 0 0-4 4v2"/><path d="m14.305 16.53.923-.382"/><path d="m15.2'
        '28 13.852-.923-.383"/><path d="m16.852 12.228-.383-.923"/><path d="m16.852 17.772-.3'
        '83.924"/><path d="m19.148 12.228.383-.923"/><path d="m19.53 18.696-.382-.924"/><path'
        ' d="m20.772 13.852.924-.383"/><path d="m20.772 16.148.924.383"/><circle cx="18" cy="'
        '15" r="3"/><circle cx="9" cy="7" r="4"/>'
    ),
    "user": ('<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>'),
    "users": (
        '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><path d="M16 3.128a4 4 0 0 1 0 '
        '7.744"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><circle cx="9" cy="7" r="4"/>'
    ),
    "wifi": (
        '<path d="M12 20h.01"/><path d="M2 8.82a15 15 0 0 1 20 0"/><path d="M5 12.859a10 10 0'
        ' 0 1 14 0"/><path d="M8.5 16.429a5 5 0 0 1 7 0"/>'
    ),
    "x": ('<path d="M18 6 6 18"/><path d="m6 6 12 12"/>'),
}


def icon(name: str, size: int = 18, label: str = "") -> Markup:
    """Inline SVG for ``name``; decorative (hidden from screen readers) unless labelled."""
    inner = _ICONS[name]
    a11y = f'role="img" aria-label="{escape(label)}"' if label else 'aria-hidden="true"'
    return Markup(
        f'<svg class="icon" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round" {a11y}>{inner}</svg>'
    )
