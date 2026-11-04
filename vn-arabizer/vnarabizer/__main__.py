"""Entry point: GUI by default, command line with arguments.

  VNArabizer.exe                       open the app
  VNArabizer.exe extract  <game>       list what would be translated
  VNArabizer.exe translate <game> [--engine nllb|google|deepl] [--source auto|ja|en] [--private]
  VNArabizer.exe apply <game>          write the Arabic patch into the game
  VNArabizer.exe remove <game>         take the patch out again
  VNArabizer.exe download-model [nllb-1.3b|nllb-600m|nllb-3.3b]
  VNArabizer.exe selftest              check that the bundled components load
"""

from __future__ import annotations

import argparse
import sys


def _cli(argv: list[str]) -> int:
    # Windows consoles default to a legacy code page that cannot print Arabic.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    from .core import pipeline
    from .core.project import Project
    from .core.settings import Settings
    from .engines import detect
    from .translators import TRANSLATORS, models

    ap = argparse.ArgumentParser(prog="VNArabizer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("extract", "translate", "apply", "remove"):
        p = sub.add_parser(name)
        p.add_argument("game")
        p.add_argument("--private", action="store_true", help="keep nothing on disk (translate+apply in one run)")
        if name == "translate":
            p.add_argument("--engine", default="nllb", choices=list(TRANSLATORS))
            p.add_argument("--source", default="auto")
            p.add_argument("--model", default=models.DEFAULT_MODEL)
            p.add_argument("--apply", action="store_true", help="apply the patch when done")
    d = sub.add_parser("download-model")
    d.add_argument("model", nargs="?", default=models.DEFAULT_MODEL, choices=list(models.MODELS))
    d.add_argument("--repo", default=None)
    sub.add_parser("selftest")
    sub.add_parser("download-gpu", help="download NVIDIA libraries for GPU translation (Windows)")
    t = sub.add_parser("test-translate")
    t.add_argument("text")
    t.add_argument("--source", default="en")
    t.add_argument("--model", default=models.DEFAULT_MODEL)
    a = ap.parse_args(argv)
    s = Settings.load()

    if a.cmd == "selftest":
        import ctranslate2
        import sentencepiece

        from .core.paths import assets_dir

        print("ctranslate2", ctranslate2.__version__, "cuda devices:", ctranslate2.get_cuda_device_count())
        print("sentencepiece", sentencepiece.__version__)
        print("fonts", sorted(p.name for p in (assets_dir() / "fonts").iterdir()))
        return 0

    if a.cmd == "download-model":
        def prog(name, done, total):
            print(f"\r{name}: {done // 2**20} / {total // 2**20} MB", end="", flush=True)

        path = models.install(a.model, prog, token=s.hf_token or None, repo=a.repo)
        print(f"\ninstalled to {path} from {models.installed_info(a.model).get('repo')}")
        return 0

    if a.cmd == "download-gpu":
        from .translators import cuda

        def gprog(name, done, total):
            print(f"\r{name}: {done // 2**20} / {total // 2**20} MB", end="", flush=True)

        path = cuda.install(gprog)
        print(f"\ninstalled to {path}:", *sorted(p.name for p in path.glob("*.dll")))
        errors = cuda.add_to_path()
        print("preload:", "ok" if not errors else errors)
        return 0

    if a.cmd == "test-translate":
        tr = TRANSLATORS["nllb"](model_key=a.model, log=print)
        print(tr.translate([a.text], a.source)[0])
        print("device:", tr.device)
        return 0

    eng = detect(a.game)
    if eng is None:
        print("unsupported or unrecognised game", file=sys.stderr)
        return 2
    print(f"{eng.root.name}: {eng.describe()}")
    if a.cmd == "remove":
        print("removed:", eng.remove())
        return 0

    project = Project(eng.root, private=a.private)
    if a.cmd in ("extract", "translate") or not project.stats()["total"]:
        added, total = project.merge_entries(eng.extract())
        print(f"{total} lines ({added} new)")
        for w in eng.warnings:
            print("warning:", w)
    if a.cmd == "translate":
        cls = TRANSLATORS[a.engine]
        tr = cls(model_key=a.model) if a.engine == "nllb" else cls(api_key=s.deepl_key) if a.engine == "deepl" else cls()
        ok, msg = tr.ready()
        if not ok:
            print(msg, file=sys.stderr)
            return 3
        st = pipeline.run(project, tr, a.source, progress=lambda d, t, m: print(f"\r{d}/{t}", end="", flush=True))
        print(f"\ntranslated {st.translated}, errors {st.errors}, {st.seconds:.0f}s")
    if a.cmd == "apply" or (a.cmd == "translate" and a.apply):
        files = eng.apply(project.translations())
        print("written:", *files, sep="\n  ")
    project.close()
    return 0


def main() -> int:
    if len(sys.argv) > 1:
        return _cli(sys.argv[1:])
    from .gui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
