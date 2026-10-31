import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import nyasan  # noqa: E402

ASSETS = json.loads((ROOT / "assets" / "library.json").read_text(encoding="utf-8"))["assets"]


def test_exact_match_prefers_bust():
    p = nyasan.pick_asset(ASSETS, "nyasan", "joy", None)
    assert p.exact and p.asset["id"] == "NY_EXP_JOY"


def test_pose_match():
    p = nyasan.pick_asset(ASSETS, "nyasan", None, "point")
    assert p.exact and p.asset["pose"] == "point"


def test_missing_expression_falls_back_to_same_character():
    p = nyasan.pick_asset(ASSETS, "shacho", "sleepy", None)
    assert not p.exact
    assert p.asset["character"] == "shacho"
    assert p.want == "shacho/sleepy"


def test_scene_seconds():
    assert nyasan.scene_seconds({"line": "あ"}) == nyasan.MIN_SCENE_SEC
    long = {"line": "あ" * 30}
    assert nyasan.scene_seconds({**long, "type": "monologue"}) > nyasan.scene_seconds(long)
    assert nyasan.scene_seconds({"line": "x", "seconds": 4}) == 4


def test_wrap_keeps_punctuation_off_line_start():
    f = nyasan.font(60)
    lines = nyasan.wrap("あいうえおかきくけこさしすせそ。たちつてと", f, 60 * 15)
    assert all(not ln.startswith("。") for ln in lines)


def test_cutout_removes_outer_white_keeps_inner_white():
    img = Image.new("RGB", (200, 200), (252, 252, 252))
    d = ImageDraw.Draw(img)
    d.ellipse((40, 40, 160, 160), fill=(120, 120, 120), outline=(0, 0, 0), width=4)
    d.ellipse((90, 90, 110, 110), fill=(255, 255, 255))  # 目のハイライト役
    out = nyasan.cutout_white(img)
    cx, cy = out.width // 2, out.height // 2
    assert out.getpixel((cx, cy))[3] == 255
    assert out.width < 200  # 外側の白が削られている


def test_episode_files_are_valid():
    settings = json.loads((ROOT / "characters" / "characters.json").read_text(encoding="utf-8"))
    chars = settings["characters"]
    threads = {t["id"] for t in settings["channel"]["storyThreads"]}
    for c in chars.values():
        assert c["appeal"]["entry"] and c["appeal"]["deep"], c["displayName"]  # 台本づくりで毎話どれかを見せる
    for path in (ROOT / "episodes").glob("*.json"):
        ep = json.loads(path.read_text(encoding="utf-8"))
        assert ep["scenes"], path
        for key, text in ep["appeal"].items():  # この回の魅力はキャラ設定の言葉から選ぶ
            assert text in chars[key]["appeal"]["entry"] + chars[key]["appeal"]["deep"], (path, text)
        assert ep["setup"]["thread"] in threads, path
        for s in ep["scenes"]:
            assert s["speaker"] in chars, path
            assert s.get("type", "line") in ("line", "monologue"), path


def test_video_scene_seconds_speeds_up_then_extends():
    # 3秒のセリフに8秒の動き → 4秒に延ばして2倍速
    assert nyasan.video_scene_seconds(3.0, 8.0) == (4.0, 2.0)
    # セリフが長ければ速度を落とす
    assert nyasan.video_scene_seconds(6.0, 8.0) == (6.0, 1.333)
    # クリップのほうが短いときは等速
    assert nyasan.video_scene_seconds(5.0, 4.0) == (5.0, 1.0)


def test_manga_layout_stacks_full_width():
    import manga
    boxes = manga.layout([{"h": 1}, {"h": 2}])
    # コマは全幅で上から積む（横に細いコマを作らない）。高さは h の比
    assert all(b[0] == manga.MARGIN and b[2] == manga.W - manga.MARGIN for b in boxes)
    assert boxes[0][3] < boxes[1][1]
    assert abs((boxes[1][3] - boxes[1][1]) - 2 * (boxes[0][3] - boxes[0][1])) <= 2


def test_manga_paginate_two_per_page():
    import manga
    p = [{"id": i} for i in range(5)]
    assert [len(x) for x in manga.paginate(p)] == [2, 2, 1]
    p[2]["solo"] = True
    assert [[q["id"] for q in x] for x in manga.paginate(p)] == [[0, 1], [2], [3, 4]]


def test_manga_vertical_lines_keep_punctuation():
    import manga
    cols = manga.vertical_lines("大丈夫です。カチカチッて", 5)
    assert all(not c.startswith(("。", "ッ")) for c in cols)
    assert "".join(cols) == "大丈夫です。カチカチッて"
