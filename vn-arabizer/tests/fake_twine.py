"""Tiny Twine 2 HTML games (SugarCube and Harlowe) for tests."""

from __future__ import annotations

import html
from pathlib import Path

SUGARCUBE_PASSAGES = {
    "StoryInit": "<<set $gold to 5>><<set $name to \"Mira\">>",
    "Start": (
        "You wake up in a small room. You have $gold coins.\n"
        "<<if $gold > 3>>You feel rich today!<</if>>\n"
        "\n"
        "''Mira'' smiles at you: \"Good morning, <<print $name>>!\"\n"
        "[[Go to the market|Market]]\n"
        "[[Bedroom]]\n"
        "<<link \"Open the window\" \"Window\">><<set $air to true>><</link>>\n"
        "<<button \"Rest\">><<goto \"Bedroom\">><</button>>\n"
        "<<script>>\nconsole.log(\"not text\");\n<</script>>\n"
        "/* a comment that is not text */"
    ),
    "Market": "The market is busy. <span class=\"note\">A merchant waves.</span>\n<<return \"Back\">>",
    "Bedroom": "Your bed looks comfortable.\n<<set $rested to true>>",
    "Story JavaScript": "",
}

HARLOWE_PASSAGES = {
    "Start": (
        "(set: $gold to 5)You have $gold coins.\n"
        "(if: $gold > 3)[You feel rich today!]\n"
        "[[Go to the market->Market]]\n"
        "[[Bedroom<-Go to bed]]\n"
        "(link-goto: \"Open the window\", \"Window\")"
    ),
    "Market": "The market is busy.",
    "Bedroom": "Your bed looks comfortable.",
    "Window": "Fresh air.",
}


def make_twine(root: Path, fmt: str = "SugarCube") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    passages = SUGARCUBE_PASSAGES if fmt == "SugarCube" else HARLOWE_PASSAGES
    parts = []
    for i, (name, text) in enumerate(passages.items(), 1):
        tags = "script" if name == "Story JavaScript" else ""
        parts.append(
            f'<tw-passagedata pid="{i}" name="{html.escape(name)}" tags="{tags}" position="0,0" size="100,100">'
            f"{html.escape(text)}</tw-passagedata>"
        )
    doc = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>Test</title>"
        "<style>body{color:#eee}</style></head><body>"
        f'<tw-storydata name="Test" startnode="2" creator="Twine" creator-version="2.6.2" format="{fmt}" '
        f'format-version="2.36.1" ifid="ABC" options="" hidden>'
        '<style role="stylesheet" id="twine-user-stylesheet" type="text/twine-css"></style>'
        '<script role="script" id="twine-user-script" type="text/twine-javascript"></script>'
        + "".join(parts)
        + "</tw-storydata><script>/* story format engine */</script></body></html>"
    )
    (root / "Test Game.html").write_text(doc, encoding="utf-8")
    (root / "img").mkdir(exist_ok=True)
    return root
