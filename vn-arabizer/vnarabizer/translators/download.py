"""Resumable HTTPS downloads with progress, cancel and checksum support."""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .. import __version__

Progress = Callable[[str, int, int], None]  # (file name, bytes done, bytes total)
USER_AGENT = f"VNArabizer/{__version__}"


class Cancelled(Exception):
    pass


class DownloadError(Exception):
    pass


def _context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi  # bundled in the Windows build

        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def get_json(url: str, token: str | None = None, timeout: float = 30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_context()) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise DownloadError(f"HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        raise DownloadError(f"cannot reach {url}: {e.reason}") from e


def download(
    url: str,
    dest: Path,
    progress: Progress | None = None,
    cancel: Callable[[], bool] = lambda: False,
    sha256: str | None = None,
    token: str | None = None,
    expected_size: int | None = None,
) -> Path:
    """Download ``url`` to ``dest``, resuming a previous partial download."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (expected_size is None or dest.stat().st_size == expected_size):
        if not sha256 or _sha256(dest) == sha256:
            return dest
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0

    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60, context=_context())
    except urllib.error.HTTPError as e:
        if e.code == 416:  # already complete
            resp = None
        else:
            raise DownloadError(f"HTTP {e.code} downloading {dest.name}") from e
    except urllib.error.URLError as e:
        raise DownloadError(f"cannot download {dest.name}: {e.reason}") from e

    if resp is not None:
        with resp:
            if have and resp.status != 206:
                have = 0  # server ignored the range; start over
            length = resp.headers.get("Content-Length")
            total = have + int(length) if length else (expected_size or 0)
            mode = "ab" if have else "wb"
            done = have
            with open(part, mode) as f:
                while True:
                    if cancel():
                        raise Cancelled()
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(dest.name, done, total)

    if expected_size is not None and part.stat().st_size != expected_size:
        raise DownloadError(f"{dest.name}: incomplete download ({part.stat().st_size} of {expected_size} bytes)")
    if sha256:
        got = _sha256(part)
        if got != sha256:
            part.unlink(missing_ok=True)
            raise DownloadError(f"{dest.name}: checksum mismatch, the file was deleted; please retry")
    os.replace(part, dest)
    return dest


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
