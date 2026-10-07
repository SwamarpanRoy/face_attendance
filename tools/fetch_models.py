"""Download and verify the InsightFace ``buffalo_s`` ONNX models into ``models/``.

Why a script instead of "download it from the website": the models are binaries that
must never be committed, and every machine (laptop, server, Pi) must load exactly the
files the stored templates were computed with. This script pins the SHA-256 of the
official release zip *and* of each model file, refuses anything else, and only moves
verified files into place, so a half-finished or tampered download can never be
mistaken for a working install.

Usage::

    python tools/fetch_models.py                      # buffalo_s.zip (~122 MB) -> models/
    python tools/fetch_models.py --pack buffalo_sc    # ~14 MB pack with the same two files
    python tools/fetch_models.py --zip buffalo_s.zip  # verify + extract a zip you already have

Only ``det_500m.onnx`` (detector) and ``w600k_mbf.onnx`` (recogniser) are extracted. The
other files in ``buffalo_s`` (landmarks, gender/age) are not used by this project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

RELEASE_BASE_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7"

#: The two files the pipeline loads. Everything else in a pack is ignored.
REQUIRED_MODELS: tuple[str, ...] = ("det_500m.onnx", "w600k_mbf.onnx")

#: SHA-256 of the model files themselves. Measured 2026-10-06; identical in
#: buffalo_s and buffalo_sc.
MODEL_SHA256: dict[str, str] = {
    "det_500m.onnx": "5e4447f50245bbd7966bd6c0fa52938c61474a04ec7def48753668a9d8b4ea3a",
    "w600k_mbf.onnx": "9cc6e4a75f0e2bf0b1aed94578f144d15175f357bdc05e815e5c4a02b319eb4f",
}

MANIFEST_NAME = "MANIFEST.json"


@dataclass(frozen=True)
class PackSpec:
    """One downloadable release zip and the hashes that make it trustworthy."""

    name: str
    url: str
    zip_sha256: str
    size_mb: int
    file_sha256: Mapping[str, str]


PACKS: dict[str, PackSpec] = {
    "buffalo_s": PackSpec(
        name="buffalo_s",
        url=f"{RELEASE_BASE_URL}/buffalo_s.zip",
        zip_sha256="d85a87f503f691807cd8bb97128bdf7a0660326cd9cd02657127fa978bab8b5e",
        size_mb=122,
        file_sha256=MODEL_SHA256,
    ),
    "buffalo_sc": PackSpec(
        name="buffalo_sc",
        url=f"{RELEASE_BASE_URL}/buffalo_sc.zip",
        zip_sha256="57d31b56b6ffa911c8a73cfc1707c73cab76efe7f13b675a05223bf42de47c72",
        size_mb=14,
        file_sha256=MODEL_SHA256,
    ),
}
DEFAULT_PACK = "buffalo_s"

Downloader = Callable[[str, Path], None]
Reporter = Callable[[str], None]


class FetchError(RuntimeError):
    """A download or verification failed. Nothing was written to the destination."""


def _silent(_message: str) -> None:
    """Default reporter for library use and tests."""


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    """Hash a file in chunks so a 140 MB zip does not need to fit in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path, *, report: Reporter = _silent) -> None:
    """Stream ``url`` to ``dest`` with a coarse progress report.

    GitHub redirects release assets to a CDN; urllib follows that. A User-Agent is set
    because GitHub rejects some default clients. Only the standard library is used so
    the script runs on a freshly provisioned Pi before anything is pip-installed.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "face-attendance-fetch-models"})
    with urllib.request.urlopen(request, timeout=60) as response, dest.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        next_mark = 0.0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total and done / total >= next_mark:
                report(f"  {done / 1e6:6.1f} / {total / 1e6:.1f} MB")
                next_mark += 0.1


def _verified(path: Path, expected_sha256: str) -> bool:
    return path.is_file() and sha256_of(path) == expected_sha256


def models_up_to_date(dest_dir: Path, spec: PackSpec) -> bool:
    """True when every required model is present with the pinned hash."""
    return all(_verified(dest_dir / name, spec.file_sha256[name]) for name in REQUIRED_MODELS)


def _extract_required(zip_path: Path, into: Path, spec: PackSpec) -> dict[str, Path]:
    """Copy the required members out of the zip by basename, verifying each one.

    Members are selected by basename so both the flat layout of the official zips and a
    nested ``buffalo_s/...`` layout work, and they are written under names *we* choose,
    which also rules out zip-slip paths from a malicious archive.
    """
    found: dict[str, Path] = {}
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = Path(info.filename).name
            if info.is_dir() or name not in REQUIRED_MODELS or name in found:
                continue
            target = into / name
            with archive.open(info) as src, target.open("wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
            actual = sha256_of(target)
            if actual != spec.file_sha256[name]:
                raise FetchError(
                    f"{name} inside {zip_path.name} has SHA-256 {actual}, "
                    f"expected {spec.file_sha256[name]}"
                )
            found[name] = target
    missing = [name for name in REQUIRED_MODELS if name not in found]
    if missing:
        raise FetchError(f"{zip_path.name} does not contain {', '.join(missing)}")
    return found


def write_manifest(dest_dir: Path, spec: PackSpec) -> Path:
    """Record what was installed so a later self-test can check integrity cheaply."""
    manifest = {
        "pack": spec.name,
        "source": spec.url,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": {name: spec.file_sha256[name] for name in REQUIRED_MODELS},
    }
    path = dest_dir / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def fetch_models(
    dest_dir: Path,
    spec: PackSpec,
    *,
    downloader: Downloader | None = None,
    zip_path: Path | None = None,
    force: bool = False,
    report: Reporter = _silent,
) -> list[Path]:
    """Ensure the verified model files exist in ``dest_dir`` and return their paths.

    The zip is downloaded (or taken from ``zip_path``), its SHA-256 checked, the two
    models extracted into a temporary directory *inside* ``dest_dir`` (same filesystem,
    so the final move is atomic) and verified individually, then moved into place. A
    user-supplied ``zip_path`` is never deleted.
    """
    fetch = downloader or download
    dest_dir.mkdir(parents=True, exist_ok=True)
    targets = [dest_dir / name for name in REQUIRED_MODELS]
    if not force and models_up_to_date(dest_dir, spec):
        report(f"models already present and verified in {dest_dir}")
        return targets

    with tempfile.TemporaryDirectory(prefix=".fetch-", dir=dest_dir) as tmp:
        work = Path(tmp)
        if zip_path is None:
            zip_path = work / f"{spec.name}.zip"
            report(f"downloading {spec.url} (~{spec.size_mb} MB)")
            fetch(spec.url, zip_path)
        actual = sha256_of(zip_path)
        if actual != spec.zip_sha256:
            raise FetchError(
                f"SHA-256 mismatch for {zip_path.name}: got {actual}, expected "
                f"{spec.zip_sha256}. Refusing to extract: the download may be corrupt "
                "or the release asset changed."
            )
        extracted = _extract_required(zip_path, work, spec)
        for name, path in extracted.items():
            path.replace(dest_dir / name)

    write_manifest(dest_dir, spec)
    report("wrote " + ", ".join(str(target) for target in targets))
    return targets


def build_parser() -> argparse.ArgumentParser:
    """Command-line options. Defaults match the repo layout and ``.env``."""
    parser = argparse.ArgumentParser(
        description="Download and verify the InsightFace face models used by this project.",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path(os.environ.get("MODELS_DIR", "models")),
        help="target directory (default: $MODELS_DIR or ./models)",
    )
    parser.add_argument(
        "--pack",
        choices=sorted(PACKS),
        default=DEFAULT_PACK,
        help=f"release pack to download (default: {DEFAULT_PACK}; buffalo_sc is the small one)",
    )
    parser.add_argument(
        "--zip",
        type=Path,
        default=None,
        help="use this already-downloaded zip instead of downloading (offline Pi)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download and overwrite even if the installed models verify",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code instead of raising, for shell use."""
    args = build_parser().parse_args(argv)
    spec = PACKS[args.pack]

    def report(message: str) -> None:
        print(message, file=sys.stderr)

    try:
        paths = fetch_models(args.dest, spec, zip_path=args.zip, force=args.force, report=report)
    except (FetchError, OSError, zipfile.BadZipFile) as exc:
        # URLError and HTTPError are OSError subclasses, so network failures land here.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
