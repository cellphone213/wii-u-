import pytest

from vnarabizer.core import protect as p


@pytest.mark.parametrize(
    "text,reason",
    [
        ("eileen happy.png", "file name"),
        ("audio/theme.ogg", "file name"),
        ("gui/frame", "path"),
        ("bg_room", "identifier"),
        ("persistent.seen_ending", "identifier"),
        ("#ff00aa", "color code"),
        ("42", "numbers/symbols only"),
        ("{w}", "markup only"),
        ("Hmm...", None),
        ("Yes", None),
        ("No.", None),
        ("I saw image.png.", None),  # prose that merely mentions a file is still prose
    ],
)
def test_untranslatable(text, reason):
    assert p.untranslatable_reason(text) == reason


def test_mask_peels_edges_and_keeps_tokens():
    m = p.mask("  {i}Hello{/i}, [player]!{w} Ready?\n")
    assert m.prefix == "  {i}" and m.suffix == "\n"
    assert m.tokens == ["{/i}", "[player]", "{w}"]
    assert "⟦0⟧" in m.body and "Ready?" in m.body


def test_unmask_validates_and_escapes():
    m = p.mask("Hello [player]!")
    out = p.unmask(m, "مرحبا ⟦0⟧! [x] {y} 50%")
    assert out == "مرحبا [player]! [[x] {{y} 50٪"
    assert p.validate(m.original, out) == []


def test_unmask_rejects_lost_placeholder():
    m = p.mask("Hi [player], {b}look{/b}!")
    with pytest.raises(p.RestoreError):
        p.unmask(m, "مرحبا، انظر!")


def test_segment_fallback_keeps_every_token_in_place():
    m = p.mask("Hi [player], {b}look{/b}!")
    segs = p.segments(m)
    out = p.join_segments(m, ["مرحبا" if s.strip() else s for s in segs])
    assert out.count("[player]") == 1 and "{b}" in out and "{/b}" in out
    assert p.validate(m.original, out) == []


def test_validate_catches_damage():
    assert p.validate("{b}Hi{/b}", "مرحبا{/b}")
    assert p.validate("Hi [name]", "مرحبا [nam")
    assert p.validate("Hi", "") == ["empty translation"]
