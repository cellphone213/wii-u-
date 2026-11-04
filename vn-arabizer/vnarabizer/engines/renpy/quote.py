"""Write Python strings as Ren'Py script string literals.

Ren'Py's lexer collapses runs of spaces and newlines into one space and then
expands backslash escapes (``\\n`` -> newline, ``\\X`` -> ``X``). ``quote``
produces a literal that lexes back to exactly the same string, so the
``old`` side of a string translation always matches what the engine looks up.
"""

from __future__ import annotations

import re


def quote(s: str) -> str:
    s = s.replace("\r", "")
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", "\\n")
    # Keep the first space of a run; escape the rest so they are not collapsed.
    s = re.sub(r"(?<= ) ", "\\\\ ", s)
    return '"' + s + '"'
