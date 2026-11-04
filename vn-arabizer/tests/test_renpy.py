import json
import re

import pytest

from fake_renpy import rpa_bytes, rpyc_bytes, sample_script
from vnarabizer.engines import detect
from vnarabizer.engines.renpy import RenPyEngine
from vnarabizer.engines.renpy.patch import strings_rpy
from vnarabizer.engines.renpy.quote import quote
from vnarabizer.engines.renpy.rpa import RPAArchive
from vnarabizer.engines.renpy.rpyc import read_rpyc


def renpy_lex_string(literal: str) -> str:
    """Copy of Ren'Py's lexer string handling (renpy/lexer.py, MIT licence)."""
    s = literal[1:-1]

    def dequote(m):
        c = m.group(1)
        if c == "{":
            return "{{"
        elif c == "[":
            return "[["
        elif c == "%":
            return "%%"
        elif c == "n":
            return "\n"
        elif c[0] == "u":
            if m.group(2):
                return chr(int(m.group(2), 16))
        else:
            return c

    s = re.sub(r"[ \n]+", " ", s)
    s = re.sub(r"\\(u([0-9a-fA-F]{1,4})|.)", dequote, s)
    return s


@pytest.mark.parametrize(
    "text",
    [
        "مرحبا",
        'He said "hi"',
        "back\\slash",
        "two  spaces   three",
        "line\nbreak",
        " leading and trailing ",
        "{b}bold{/b} [name] 50%%",
        "tab\tchar",
    ],
)
def test_quote_roundtrip(text):
    assert renpy_lex_string(quote(text)) == text


@pytest.fixture(params=["cslots", "dict"])
def game(tmp_path, request):
    root = tmp_path / "MyGame"
    (root / "game").mkdir(parents=True)
    (root / "renpy" / "common").mkdir(parents=True)
    (root / "renpy" / "vc_version.py").write_text("version = '8.3.7.25031702'\n")
    (root / "lib" / "py3-windows-x86_64").mkdir(parents=True)
    script = sample_script(request.param)
    (root / "game" / "script.rpyc").write_bytes(rpyc_bytes(script))
    return root


def test_detect_and_version(game):
    eng = detect(game)
    assert isinstance(eng, RenPyEngine)
    assert eng.version == "8.3.7.25031702"
    assert eng.python_major == 3
    # Selecting the "game" folder itself also works.
    assert isinstance(detect(game / "game"), RenPyEngine)


def test_extract(game):
    entries = {(e.channel, e.original): e for e in detect(game).extract()}
    say = {o for (c, o) in entries if c == "say"}
    strings = {o for (c, o) in entries if c == "strings"}

    assert "Hello, [player]! Welcome to {b}Ren'Py{/b}." in say
    assert "The sun was setting." in say
    assert "Go left" in say and "Go right" in say
    assert "What should I do?" in say
    # Translations into other languages are not source text.
    assert "Hallo!" not in say
    # Names and screen text.
    assert {"Eileen", "Mysterious Man", "Start Game", "Load"} <= strings
    # Never extracted: image names, code, file names.
    joined = " ".join(say | strings)
    assert "eileen happy" not in joined
    assert "gui/logo.png" not in joined
    assert 'ShowMenu("load")' not in joined
    # Speaker variable resolved to its display name.
    assert entries[("say", "Hello, [player]! Welcome to {b}Ren'Py{/b}.")].speaker == "Eileen"


def test_legacy_rpyc_and_archive(tmp_path):
    root = tmp_path / "Old"
    (root / "game").mkdir(parents=True)
    script = rpyc_bytes(sample_script("dict"), protocol=2, legacy=True)
    (root / "game" / "archive.rpa").write_bytes(rpa_bytes({"script.rpyc": script, "images/bg.png": b"\x89PNG"}))
    arc = RPAArchive(root / "game" / "archive.rpa")
    assert arc.names() == ["images/bg.png", "script.rpyc"]
    assert arc.read("images/bg.png") == b"\x89PNG"
    data, stmts = read_rpyc(arc.read("script.rpyc"))
    assert data["version"] == 5003000
    entries = detect(root).extract()
    assert any(e.original == "The sun was setting." for e in entries)


def test_unknown_classes_are_never_imported(tmp_path):
    import pickle

    # A pickle that would call os.system if loaded normally.
    evil = b"cos\nsystem\n(S'echo pwned'\ntR."
    from vnarabizer.engines.renpy.safepickle import loads

    obj = loads(evil)
    assert "pwned" not in repr(type(obj))
    assert type(obj).__module__.startswith("vnarabizer.fake")


def test_apply_and_remove(game):
    eng = detect(game)
    trans = {
        ("say", "The sun was setting."): "كانت الشمس تغرب.",
        ("say", "Go left"): "اذهب يساراً",
        ("strings", "Eileen"): "آيلين",
        ("strings", "Start Game"): 'ابدأ "اللعبة"',
    }
    files = eng.apply(trans)
    g = game / "game"
    assert (g / "zz_vnarabizer.rpy").exists()
    say = json.loads((g / "vnarabizer" / "say.json").read_text("utf-8"))
    assert say["The sun was setting."] == "كانت الشمس تغرب."
    rpy = (g / "tl" / "arabic" / "vnarabizer_strings.rpy").read_text("utf-8")
    assert "translate arabic strings:" in rpy
    assert "translate arabic style say_dialogue:\n    text_align 1.0" in rpy
    assert 'new "ابدأ \\"اللعبة\\""' in rpy
    assert (g / "vnarabizer" / "VNArabic-Regular.ttf").exists()
    main = (g / "zz_vnarabizer.rpy").read_text("utf-8")
    assert "config.rtl = True" in main and "say_menu_text_filter" in main
    # The generated Python must be valid on both Python 2 and 3 syntax-wise (no f-strings).
    py = "\n".join(l[4:] for l in main.splitlines() if l.startswith("    "))
    compile(py, "zz_vnarabizer", "exec")
    assert eng.is_applied()

    # Simulate Ren'Py compiling the files, then remove everything.
    (g / "zz_vnarabizer.rpyc").write_bytes(b"x")
    (g / "tl" / "arabic" / "vnarabizer_strings.rpyc").write_bytes(b"x")
    eng.remove()
    assert not eng.is_applied()
    assert not (g / "zz_vnarabizer.rpyc").exists()
    assert not (g / "vnarabizer").exists()
    assert not (g / "tl" / "arabic").exists()
    assert (g / "script.rpyc").exists()


def test_existing_arabic_translation_is_not_clobbered(game):
    (game / "game" / "tl" / "arabic").mkdir(parents=True)
    (game / "game" / "tl" / "arabic" / "common.rpy").write_text("# theirs")
    eng = detect(game)
    eng.apply({("strings", "Load"): "تحميل"})
    assert (game / "game" / "tl" / "arabic_vna" / "vnarabizer_strings.rpy").exists()
    assert (game / "game" / "tl" / "arabic" / "common.rpy").exists()
    eng.remove()
    assert (game / "game" / "tl" / "arabic" / "common.rpy").exists()


def test_strings_rpy_unique_and_escaped():
    out = strings_rpy({"a  b": ("ا  ب", "x:1"), 'q"': ("ق", "")}, "arabic")
    assert out.count("old ") == 2
    assert 'old "a \\ b"' in out
