"""Tokenizer for Twine passage source (SugarCube 2 and Harlowe 3).

Splits passage text into *code* spans (macros, HTML tags, links, variables,
comments, formatting marks) and prose. Only prose is ever translated; link
labels and the visible text of link macros are handled separately so that
passage names (link targets) are never changed.
"""

from __future__ import annotations

import re

_STR = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`', re.S)
_HTML_OPEN = re.compile(r"</?[A-Za-z][\w:-]*")
_ENTITY = re.compile(r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z]\w*);")
_VAR = re.compile(r"[$_][A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[(?:\d+|\"[^\"]*\"|'[^']*')\])*")
_HARLOWE_MACRO = re.compile(r"\([\w-]+:")
_LINE_START = re.compile(r"(?m)^[ \t]*(?:!{1,6}|\*+(?=\s)|#+(?=\s)|>+(?=\s)|-{3,}\s*$|={3,}\s*$)")
_FORMAT_PAIRS = ("''", "//", "__", "~~", "^^", "==", "**")

MACRO_LINK = {"link", "button", "linkappend", "linkprepend", "linkreplace", "choice", "click", "back", "return", "option", "actions"}
HARLOWE_LINK = {
    "link", "link-goto", "link-reveal", "link-repeat", "link-rerun", "link-undo", "link-show", "link-reveal-goto",
    "link-storylet", "link-fullscreen", "cycling-link", "seq-link", "dropdown", "linkgoto", "link-replace",
}
CYCLING = {"cycling-link", "seq-link", "dropdown", "actions"}


def _skip_string(text: str, i: int) -> int:
    m = _STR.match(text, i)
    return m.end() if m else i + 1


def _find_close(text: str, i: int, close: str) -> int:
    """Index just past ``close``, skipping quoted strings. len(text) if missing."""
    n = len(text)
    while i < n:
        if text.startswith(close, i):
            return i + len(close)
        c = text[i]
        if c in "\"'`":
            i = _skip_string(text, i)
        else:
            i += 1
    return n


def _balanced_paren(text: str, i: int) -> int:
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'`":
            i = _skip_string(text, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def spans(text: str, dialect: str = "sugarcube") -> list[tuple[int, int]]:
    """(start, end) of every code span in ``text``."""
    harlowe = dialect == "harlowe"
    out: list[tuple[int, int]] = []
    starts = {m.start(): m.end() for m in _LINE_START.finditer(text)}
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        j = 0
        if i in starts:
            j = starts[i]
        elif text.startswith("<<", i):
            name = re.match(r"<<\s*/?\s*([\w-]+)", text[i:])
            if name and name.group(1) == "script" and not text.startswith("<</", i):
                end = text.find("<</script>>", i)
                j = n if end < 0 else end + len("<</script>>")
            else:
                j = _find_close(text, i + 2, ">>")
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            j = n if end < 0 else end + 2
        elif text.startswith("<!--", i):
            end = text.find("-->", i + 4)
            j = n if end < 0 else end + 3
        elif text.startswith("/%", i):
            end = text.find("%/", i + 2)
            j = n if end < 0 else end + 2
        elif text.startswith("[[", i):
            end = text.find("]]", i + 2)
            j = n if end < 0 else end + 2
            if j < n and text[j] == "[":  # SugarCube setter: [[text|target][$x to 1]]
                end2 = text.find("]", j + 1)
                if end2 >= 0:
                    j = end2 + 1
                    if j < n and text[j] == "]":
                        j += 1
        elif text.startswith("[img[", i):
            end = text.find("]]", i)
            j = n if end < 0 else end + 2
        elif c == "<" and _HTML_OPEN.match(text, i):
            tag = _HTML_OPEN.match(text, i).group(0).lstrip("</").lower()
            j = _find_close(text, i + 1, ">")
            if tag in ("script", "style") and not text.startswith("</", i):
                end = text.lower().find(f"</{tag}", j)
                j = n if end < 0 else _find_close(text, end, ">")
        elif harlowe and c == "<" and re.match(r"<[A-Za-z_][\w-]*\|", text[i:]):
            j = text.index("|", i) + 1  # hook name <name|
        elif text.startswith("@@", i):
            m = re.match(r"@@[^;\n@&<>]*;", text[i:])
            j = i + (m.end() if m else 2)
        elif text.startswith(_FORMAT_PAIRS, i):
            j = i + 2
        elif c in "$_" and _VAR.match(text, i) and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            j = _VAR.match(text, i).end()
        elif c == "&" and _ENTITY.match(text, i):
            j = _ENTITY.match(text, i).end()
        elif harlowe and c == "(" and _HARLOWE_MACRO.match(text, i):
            j = _balanced_paren(text, i)
        elif harlowe and c == "|" and re.match(r"\|[A-Za-z_][\w-]*>", text[i:]):
            j = text.index(">", i) + 1
        elif harlowe and c == "?" and re.match(r"\?[A-Za-z_]\w*", text[i:]) and (i == 0 or not text[i - 1].isalnum()):
            j = re.match(r"\?[A-Za-z_]\w*", text[i:]).end() + i
        elif harlowe and c in "[]{}":
            j = i + 1  # Harlowe hooks / collapsing whitespace; literal brackets are kept as-is
        if j:
            out.append((i, max(j, i + 1)))
            i = max(j, i + 1)
        else:
            i += 1
    return out


def tokens(text: str, dialect: str = "sugarcube") -> list[str]:
    return [text[a:b] for a, b in spans(text, dialect)]


# -- words that stand for a value (swapped for numbers while translating) ----
_PRINT = re.compile(r"^<<\s*(?:print|=|-)\b|^\(\s*print\s*:")


def is_value_token(tok: str) -> bool:
    return bool(_VAR.fullmatch(tok) or _PRINT.match(tok) or re.fullmatch(r"\?[A-Za-z_]\w*", tok) or tok.startswith("[["))


# -- escaping prose so it is shown literally ----------------------------------
def escape_prose(s: str) -> str:
    s = re.sub(r"\s*\n\s*", " ", s)
    s = s.replace("&", "&amp;") if re.search(r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z]\w*);", s) else s
    s = s.replace("<", "&lt;").replace("$", "&#36;")
    s = s.replace("[", "&#91;").replace("]", "&#93;")
    for pair in _FORMAT_PAIRS + ("@@",):
        s = s.replace(pair, f"&#{ord(pair[0])};{pair[1]}")
    s = re.sub(r"\((?=[\w-]+:)", "&#40;", s)
    return s


# -- links and link macros -----------------------------------------------------
def split_link(tok: str, dialect: str = "sugarcube") -> tuple[str, callable] | None:
    """For a [[...]] link return (label, rebuild(new_label) -> token)."""
    if not tok.startswith("[[") or "]]" not in tok:
        return None
    close = tok.index("]]")
    inner, rest = tok[2:close], tok[close:]
    if "|" in inner:
        label, target = inner.split("|", 1)
        return label, lambda new: f"[[{new}|{target}{rest}"
    if "->" in inner:
        label, target = inner.rsplit("->", 1)
        return label, lambda new: f"[[{new}->{target}{rest}"
    if "<-" in inner:
        target, label = inner.split("<-", 1)
        return label, lambda new: f"[[{target}<-{new}{rest}"
    # [[Target]]: the label was the passage name; keep the name as the target.
    sep = "->" if dialect == "harlowe" else "|"
    return inner, lambda new: f"[[{new}{sep}{inner}{rest}" if new != inner else tok


def macro_labels(tok: str, dialect: str = "sugarcube") -> list[tuple[int, int, str]]:
    """Visible string arguments of link-like macros: (start, end, text) of each quoted label."""
    if dialect == "harlowe":
        m = re.match(r"\(\s*([\w-]+)\s*:", tok)
        if not m or m.group(1).lower() not in HARLOWE_LINK:
            return []
        name, body_start = m.group(1).lower(), m.end()
    else:
        m = re.match(r"<<\s*([\w-]+)", tok)
        if not m or m.group(1).lower() not in MACRO_LINK:
            return []
        name, body_start = m.group(1).lower(), m.end()
    out = []
    for sm in _STR.finditer(tok, body_start):
        raw = sm.group(0)
        q = raw[0]
        val = raw[1:-1].replace("\\" + q, q).replace("\\\\", "\\")
        out.append((sm.start(), sm.end(), val))
        if name not in CYCLING:
            break  # only the first string is the visible label
    return out


def quote_like(original: str, value: str) -> str:
    q = original[0]
    return q + value.replace("\\", "\\\\").replace(q, "\\" + q) + q
