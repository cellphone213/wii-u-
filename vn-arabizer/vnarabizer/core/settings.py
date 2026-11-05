"""App settings, stored as JSON in the app data folder."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields

from .paths import data_dir


@dataclass
class Settings:
    translator: str = "nllb"
    model: str = "nllb-1.3b"
    model_repo: str = ""  # optional manual Hugging Face repo
    device: str = "auto"  # auto | cuda | cpu
    beam_size: int = 4
    source_language: str = "auto"
    google_interval: float = 1.5
    deepl_key: str = ""
    hf_token: str = ""
    private_mode: bool = False
    align_right: bool = True
    force_language: bool = True
    replace_fonts: bool = True
    # Japanese engines: message width in full-width characters (0 = engine default).
    legacy_line_em: float = 0
    last_game: str = ""

    @classmethod
    def load(cls) -> "Settings":
        f = data_dir() / "settings.json"
        s = cls()
        if f.exists():
            try:
                data = json.loads(f.read_text("utf-8"))
                for fld in fields(cls):
                    if fld.name in data:
                        setattr(s, fld.name, data[fld.name])
            except Exception:
                pass
        return s

    def save(self) -> None:
        data = asdict(self)
        if self.private_mode:
            data["last_game"] = ""
        (data_dir() / "settings.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
