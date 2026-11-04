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
_loaded: list[object] = []  # keep handles alive for the life of the process

# Load order matters: cuBLAS needs cuBLASLt, which needs the CUDA runtime.
PRELOAD = ("cudart64_*.dll", "cublasLt64_*.dll", "cublas64_*.dll")


def add_to_path() -> list[str]:
    """Make the downloaded DLLs available to CTranslate2 (call before loading a model).

    Besides adding the folder to the search path, each DLL is loaded by its
    full path. CTranslate2 asks Windows for "cublas64_12.dll" by name, and a
    DLL that is already loaded is always found, whatever the search rules.
    Returns any load errors (empty list means everything loaded).
    """
    global _added
    errors: list[str] = []
    if _added or sys.platform != "win32" or not installed():
        return errors
    d = cuda_dir()
    os.add_dll_directory(str(d))
    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    import ctypes

    for pattern in PRELOAD:
        for dll in sorted(d.glob(pattern)):
            try:
                _loaded.append(ctypes.WinDLL(str(dll)))
            except OSError as e:
                errors.append(f"{dll.name}: {e}")
    _added = True
    return errors


def gpu_present() -> bool:
    """True if an NVIDIA GPU with a working driver is visible to CTranslate2."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


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
