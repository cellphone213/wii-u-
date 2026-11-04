"""Optional NVIDIA GPU support for the offline translator on Windows.

CTranslate2 needs NVIDIA's cuBLAS libraries to run on the graphics card.
They are large (hundreds of MB), so they are not in the installer; the
"enable GPU" button downloads NVIDIA's official redistributable wheels from
PyPI and keeps only the DLLs.
"""

from __future__ import annotations

import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Callable

from ..core.paths import data_dir
from .download import DownloadError, Progress, download, get_json

PACKAGES = ("nvidia-cublas-cu12", "nvidia-cuda-runtime-cu12")


def cuda_dir() -> Path:
    return data_dir() / "cuda"


def installed() -> bool:
    d = cuda_dir()
    return d.is_dir() and any(d.glob("cublas64_*.dll"))


_added = False


def add_to_path() -> None:
    """Make the downloaded DLLs visible to CTranslate2 (call before importing it)."""
    global _added
    if _added or sys.platform != "win32" or not installed():
        return
    os.add_dll_directory(str(cuda_dir()))
    os.environ["PATH"] = str(cuda_dir()) + os.pathsep + os.environ.get("PATH", "")
    _added = True


def _wheel_url(package: str) -> tuple[str, str, int]:
    info = get_json(f"https://pypi.org/pypi/{package}/json")
    if not info:
        raise DownloadError(f"{package} not found on PyPI")

    def vkey(v: str):
        return [int(p) if p.isdigit() else -1 for p in v.split(".")]

    # The newest release does not always ship a Windows wheel; take the newest one that does.
    for version in sorted(info.get("releases", {}), key=vkey, reverse=True):
        for f in info["releases"][version]:
            if f["filename"].endswith("win_amd64.whl") and not f.get("yanked"):
                return f["url"], f["digests"]["sha256"], f["size"]
    raise DownloadError(f"no Windows build of {package}")


def install(progress: Progress | None = None, cancel: Callable[[], bool] = lambda: False) -> Path:
    if sys.platform != "win32":
        raise DownloadError("GPU download is only needed on Windows")
    dest = cuda_dir()
    dest.mkdir(parents=True, exist_ok=True)
    tmp = data_dir() / "cuda-download"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for pkg in PACKAGES:
            url, sha, size = _wheel_url(pkg)
            whl = download(url, tmp / url.rsplit("/", 1)[-1], progress, cancel, sha, None, size)
            with zipfile.ZipFile(whl) as z:
                for name in z.namelist():
                    if name.lower().endswith(".dll"):
                        target = dest / Path(name).name
                        with z.open(name) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest


def remove() -> None:
    shutil.rmtree(cuda_dir(), ignore_errors=True)
