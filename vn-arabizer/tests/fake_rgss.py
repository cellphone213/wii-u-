"""Synthetic RPG Maker XP / VX Ace games built with VN Arabizer's own Marshal writer."""

from __future__ import annotations

import zlib
from pathlib import Path

from vnarabizer.archives import rgss
from vnarabizer.archives.rmarshal import RArray, RHash, RObject, RString, RUserDef, Sym, dumps


def s(text: str, enc: bool = True) -> RString:
    return RString(text.encode("utf-8"), [(Sym("E"), True)] if enc else None)


def obj(cls: str, **ivars) -> RObject:
    return RObject(Sym(cls), [(Sym("@" + k), v) for k, v in ivars.items()])


def cmd(code, params, indent=0):
    return obj("RPG::EventCommand", code=code, indent=indent, parameters=RArray(params))


VOCAB = '''module Vocab
  ShopBuy         = "購入する"
  Emerge          = "%sが出現！"
  UseItem         = "%sは%sを使った！"
  ObtainGold      = "お金を %s\\\\G 手に入れた！"
  def self.basic(id); $data_system.terms.basic[id]; end
end
'''


def vxace_files(enc=True) -> dict[str, bytes]:
    S = lambda t: s(t, enc)
    events = [
        cmd(101, [S("Actor1"), 0, 0, 2]),
        cmd(401, [S("\\N[1]「こんにちは、世界！」")]),
        cmd(401, [S("今日は\\C[2]いい天気\\C[0]ですね。")]),
        cmd(102, [RArray([S("はい"), S("いいえ")]), 2]),
        cmd(402, [0, S("はい")]),
        cmd(355, [S("$game_variables[1] = 'テスト'")]),
        cmd(320, [1, S("ハロルド")]),
        cmd(0, []),
    ]
    page = obj("RPG::Event::Page", condition=None, list=RArray(events))
    ev = obj("RPG::Event", id=1, name=S("EV001"), x=1, y=2, pages=RArray([page]))
    mp = obj("RPG::Map", display_name=S("森の小道"), width=17, data=RUserDef(Sym("Table"), b"\x01\x02"), events=RHash([(1, ev)]), note=S("<memo>"))
    actors = RArray([None, obj("RPG::Actor", id=1, name=S("ハロルド"), nickname=S("勇者"), description=S("遠い国から来た。\r\n強い。"), note=S("<tag>"))])
    terms = obj("RPG::System::Terms", basic=RArray([S("レベル"), S("Lv")]), params=RArray([S("最大ＨＰ")]), etypes=RArray([S("武器")]), commands=RArray([S("戦う")]))
    system = obj("RPG::System", game_title=S("テストゲーム"), currency_unit=S("Ｇ"), elements=RArray([S(""), S("炎")]), skill_types=RArray([S(""), S("魔法")]), weapon_types=RArray([]), armor_types=RArray([]), terms=terms)
    scripts = RArray([
        RArray([1, S("Vocab"), RString(zlib.compress(VOCAB.encode("utf-8")))]),
        RArray([2, S("Main"), RString(zlib.compress(b"rgss_main { SceneManager.run }"))]),
    ])
    common = RArray([None, obj("RPG::CommonEvent", id=1, name=S(""), list=RArray([cmd(101, [S(""), 0, 0, 2]), cmd(401, [S("さようなら。")]), cmd(0, [])]))])
    return {
        "Data/Map001.rvdata2": dumps(mp),
        "Data/Actors.rvdata2": dumps(actors),
        "Data/System.rvdata2": dumps(system),
        "Data/Scripts.rvdata2": dumps(scripts),
        "Data/CommonEvents.rvdata2": dumps(common),
        "Data/MapInfos.rvdata2": dumps(RHash([])),
        "Graphics/System/Window.png": b"\x89PNG fake",
    }


def make_vxace_game(root: Path, archive: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    files = vxace_files()
    if archive:
        rgss.build(root / "Game.rgss3a", list(files.items()), 3)
    else:
        for name, data in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
    (root / "Game.ini").write_text("[Game]\nScripts=Data\\Scripts.rvdata2\n")
    return root


def make_xp_game(root: Path) -> Path:
    """XP: Show Text (101) holds the first line itself; strings have no encoding ivars."""
    root.mkdir(parents=True, exist_ok=True)
    events = [cmd(101, [s("「こんにちは」", False)]), cmd(401, [s("元気？", False)]), cmd(0, [])]
    common = RArray([None, obj("RPG::CommonEvent", id=1, name=s("", False), list=RArray(events))])
    scripts = RArray([RArray([7, s("Main", False), RString(zlib.compress(b"begin; end"))])])
    files = {"Data/CommonEvents.rxdata": dumps(common), "Data/Scripts.rxdata": dumps(scripts)}
    rgss.build(root / "Game.rgssad", list(files.items()), 1)
    return root
