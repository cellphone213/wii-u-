"""Offline NLLB-200 models: catalog, download and lookup.

The models run with CTranslate2, so the app downloads a CTranslate2
conversion of Meta's NLLB-200 from Hugging Face plus the original
SentencePiece tokenizer. Nothing is sent anywhere while translating.

NLLB-200 is a plain translation model: it has no content filter and
translates any text it is given.

Licence note: NLLB-200 weights are CC-BY-NC 4.0 (non-commercial use).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.paths import models_dir
from .download import DownloadError, Progress, download, get_json

HF = "https://huggingface.co"
VOCAB_FILES = ("shared_vocabulary.json", "shared_vocabulary.txt")
TOKENIZER = "sentencepiece.bpe.model"


@dataclass
class ModelSpec:
    key: str
    title: str
    size_hint: str
    search: str
    tokenizer_repo: str
    # Known CTranslate2 conversions, tried in order before searching.
    candidates: list[str] = field(default_factory=list)


MODELS: dict[str, ModelSpec] = {
    "nllb-1.3b": ModelSpec(
        "nllb-1.3b",
        "NLLB-200 distilled 1.3B (موصى به)",
        "الحجم 1.4 إلى 2.7 جيجا",
        "nllb-200-distilled-1.3B",
        "facebook/nllb-200-distilled-1.3B",
        [
            "JustFrederik/nllb-200-distilled-1.3B-ct2-int8",
            "JustFrederik/nllb-200-distilled-1.3B-ct2-float16",
            "michaelfeil/ct2fast-nllb-200-distilled-1.3B",
            "OpenNMT/nllb-200-distilled-1.3B-ct2-int8",
            "entai2965/nllb-200-distilled-1.3B-ctranslate2",
        ],
    ),
    "nllb-600m": ModelSpec(
        "nllb-600m",
        "NLLB-200 distilled 600M (أخف وأسرع)",
        "الحجم 0.6 إلى 1.3 جيجا",
        "nllb-200-distilled-600M",
        "facebook/nllb-200-distilled-600M",
        [
            "JustFrederik/nllb-200-distilled-600M-ct2-int8",
            "JustFrederik/nllb-200-distilled-600M-ct2-float16",
            "michaelfeil/ct2fast-nllb-200-distilled-600M",
            "OpenNMT/nllb-200-distilled-600M-ct2-int8",
            "entai2965/nllb-200-distilled-600M-ctranslate2",
        ],
    ),
    "nllb-3.3b": ModelSpec(
        "nllb-3.3b",
        "NLLB-200 3.3B (أدق وأبطأ)",
        "الحجم 3.3 إلى 6.6 جيجا",
        "nllb-200-3.3B",
        "facebook/nllb-200-3.3B",
        [
            "JustFrederik/nllb-200-3.3B-ct2-int8",
            "JustFrederik/nllb-200-3.3B-ct2-float16",
            "michaelfeil/ct2fast-nllb-200-3.3B",
            "entai2965/nllb-200-3.3B-ctranslate2",
        ],
    ),
}

DEFAULT_MODEL = "nllb-1.3b"


def model_path(key: str) -> Path:
    return models_dir() / key


def is_installed(key: str) -> bool:
    p = model_path(key)
    return (p / "model.bin").exists() and (p / TOKENIZER).exists() and any((p / v).exists() for v in VOCAB_FILES)


def installed_info(key: str) -> dict:
    f = model_path(key) / "vnarabizer_model.json"
    if f.exists():
        try:
            return json.loads(f.read_text("utf-8"))
        except Exception:
            pass
    return {}


def remove(key: str) -> None:
    shutil.rmtree(model_path(key), ignore_errors=True)


def _repo_files(repo: str, token: str | None) -> dict[str, dict] | None:
    """Files of a Hugging Face repo with size/sha256, or None if it does not exist."""
    info = get_json(f"{HF}/api/models/{repo}?blobs=true", token)
    if not info or "siblings" not in info:
        return None
    out = {}
    for s in info["siblings"]:
        lfs = s.get("lfs") or {}
        out[s["rfilename"]] = {"size": s.get("size") or lfs.get("size"), "sha256": lfs.get("sha256")}
    return out


def _usable(files: dict[str, dict]) -> bool:
    return "model.bin" in files and "config.json" in files and any(v in files for v in VOCAB_FILES)


def find_source(spec: ModelSpec, token: str | None = None, log: Callable[[str], None] = print) -> tuple[str, dict]:
    """Pick a Hugging Face repo with a CTranslate2 conversion of the model."""
    tried = []
    for repo in spec.candidates:
        log(f"checking {repo}")
        files = _repo_files(repo, token)
        tried.append(repo)
        if files and _usable(files):
            return repo, files
    log("searching Hugging Face for a CTranslate2 conversion")
    results = get_json(f"{HF}/api/models?search={spec.search}&filter=ctranslate2&sort=downloads&direction=-1&limit=30", token) or []
    for r in results:
        repo = r.get("id") or r.get("modelId")
        if not repo or repo in tried or spec.search.lower() not in repo.lower():
            continue
        files = _repo_files(repo, token)
        if files and _usable(files):
            return repo, files
    raise DownloadError(
        "No downloadable copy of this model was found on Hugging Face. "
        "Check your internet connection, or set a repository manually in Settings."
    )


def install(
    key: str,
    progress: Progress | None = None,
    cancel: Callable[[], bool] = lambda: False,
    token: str | None = None,
    repo: str | None = None,
    log: Callable[[str], None] = print,
) -> Path:
    spec = MODELS[key]
    if repo:
        files = _repo_files(repo, token)
        if not files or not _usable(files):
            raise DownloadError(f"{repo} is not a CTranslate2 model repository")
    else:
        repo, files = find_source(spec, token, log)
    log(f"downloading from {repo}")
    dest = model_path(key)
    dest.mkdir(parents=True, exist_ok=True)

    wanted = ["config.json", "model.bin"] + [v for v in VOCAB_FILES if v in files]
    for extra in ("tokenizer_config.json", "special_tokens_map.json"):
        if extra in files:
            wanted.append(extra)
    for name in wanted:
        meta = files.get(name, {})
        download(f"{HF}/{repo}/resolve/main/{name}", dest / name, progress, cancel, meta.get("sha256"), token, meta.get("size"))

    # The SentencePiece model: from the same repo if present, else from Meta's original repo.
    if TOKENIZER in files:
        meta = files[TOKENIZER]
        download(f"{HF}/{repo}/resolve/main/{TOKENIZER}", dest / TOKENIZER, progress, cancel, meta.get("sha256"), token, meta.get("size"))
    else:
        tfiles = _repo_files(spec.tokenizer_repo, token) or {}
        meta = tfiles.get(TOKENIZER, {})
        download(
            f"{HF}/{spec.tokenizer_repo}/resolve/main/{TOKENIZER}", dest / TOKENIZER, progress, cancel, meta.get("sha256"), token, meta.get("size")
        )

    (dest / "vnarabizer_model.json").write_text(json.dumps({"repo": repo, "key": key}, indent=1), "utf-8")
    return dest
