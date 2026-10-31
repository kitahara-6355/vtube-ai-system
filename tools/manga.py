#!/usr/bin/env python3
"""にゃーさんショートを「動く漫画」として書き出す（制作の第1段階）。

  python tools/manga.py sheet  episodes/EP001_mouse.json   # コマ一覧（ページごとの完成形）を画像で出す
  python tools/manga.py prep   episodes/EP001_mouse.json   # 足りない絵の生成指示書と、Flow のコマ画像・依頼文を作る
  python tools/manga.py render episodes/EP001_mouse.json   # 縦型ショート(1080x1920)を書き出す

台本の各シーンが漫画の1ページ。シーンの "panels" に1コマずつ書く。コマは決めた秒数で順に現れ、
シーンが変わるとページをめくる。秒数とコマ順はここで固定し、あとから絵（第2段階）や
Flow の動画（第3段階）に1コマずつ置き換える。外部APIは呼ばない。
"""
from __future__ import annotations

import argparse
import math
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nyasan as ny  # noqa: E402

W, H, FPS = ny.W, ny.H, ny.FPS
PAGE_TOP, PAGE_BOTTOM, MARGIN, GUTTER = 250, 1690, 36, 22
FADE, TURN = 0.18, 0.35          # コマが現れる秒数、ページをめくる秒数
ZOOM = 0.035                     # 表示中のコマをゆっくり寄せる量
INK, PAPER = (20, 20, 20), (255, 255, 255)
VERT_FONT = "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"
ZEN = str.maketrans("0123456789!?", "０１２３４５６７８９！？")


def vfont(size: int) -> ImageFont.FreeTypeFont:
    path = VERT_FONT if Path(VERT_FONT).exists() else next(p for p in ny.FONT_CANDIDATES if Path(p).exists())
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)


# ---------- コマの中身 ----------

_cut_cache: dict[str, Image.Image] = {}


def source(asset_id: str, assets: dict) -> Image.Image:
    """素材を切り抜き済み（白背景なら透過）で返す。座標は元画像のまま。"""
    if asset_id not in _cut_cache:
        img = Image.open(ny.IMG_DIR / f"{asset_id}.png")
        if assets.get(asset_id, {}).get("bg") == "scene":
            _cut_cache[asset_id] = img.convert("RGBA")
        else:
            _cut_cache[asset_id] = ny.cutout_white(img, crop=False)
    return _cut_cache[asset_id]


def speed_lines(size: tuple[int, int], dense: int = 90, seed: int = 1, color=INK) -> Image.Image:
    """集中線。中心をあけて外側から黒い楔を描く。"""
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = w / 2, h * 0.42
    rng = np.random.default_rng(seed)
    R = math.hypot(w, h)
    for i in range(dense):
        a = 2 * math.pi * i / dense + rng.uniform(-0.03, 0.03)
        inner = R * rng.uniform(0.26, 0.36)
        width = rng.uniform(0.006, 0.016)
        p1 = (cx + math.cos(a - width) * R, cy + math.sin(a - width) * R)
        p2 = (cx + math.cos(a + width) * R, cy + math.sin(a + width) * R)
        tip = (cx + math.cos(a) * inner, cy + math.sin(a) * inner)
        d.polygon([p1, p2, tip], fill=color + (255,))
    return img


def tone(size: tuple[int, int], dark: bool = False) -> Image.Image:
    """スクリーントーン（網点）。"""
    w, h = size
    img = Image.new("RGB", size, (236, 236, 236) if not dark else (88, 92, 110))
    d = ImageDraw.Draw(img)
    dot = (200, 200, 200) if not dark else (60, 64, 82)
    for y in range(0, h, 14):
        for x in range((y // 14) % 2 * 7, w, 14):
            d.ellipse((x - 2.5, y - 2.5, x + 2.5, y + 2.5), fill=dot)
    return img


def draw_screen(size: tuple[int, int], panel: dict) -> Image.Image:
    """パソコン画面のアップ（漫画のコマ用の描き文字ならぬ描き画面）。素材がなくても描ける。"""
    w, h = size
    img = Image.new("RGB", size, PAPER)
    d = ImageDraw.Draw(img)
    m = int(min(w, h) * 0.07)
    x0, y0, x1, y1 = m, m, w - m, h - m
    d.rounded_rectangle((x0, y0, x1, y1), radius=24, fill=(52, 56, 66), outline=INK, width=8)
    sx0, sy0, sx1, sy1 = x0 + 26, y0 + 26, x1 - 26, y1 - 26
    d.rectangle((sx0, sy0, sx1, sy1), fill=(236, 244, 252), outline=INK, width=4)
    bar = int((sy1 - sy0) * 0.09)
    d.rectangle((sx0, sy0, sx1, sy0 + bar), fill=(70, 120, 200))
    for k in range(3):
        cx = sx1 - 30 - k * 40
        d.ellipse((cx - 11, sy0 + bar / 2 - 11, cx + 11, sy0 + bar / 2 + 11), fill=PAPER)
    rows = 9
    for r in range(rows):
        y = sy0 + bar + 30 + r * (sy1 - sy0 - bar - 50) / rows
        ln = 0.55 + 0.35 * ((r * 37) % 10) / 10
        d.rounded_rectangle((sx0 + 34, y, sx0 + 34 + (sx1 - sx0 - 80) * ln, y + 16), radius=8, fill=(160, 172, 190))
    if panel.get("scroll"):
        for k in range(14):
            x = sx0 + 20 + k * (sx1 - sx0 - 40) / 13
            d.line((x, sy0 + bar + 10, x, sy1 - 10), fill=(120, 130, 150), width=3)
    if panel.get("cursor"):
        cx = sx0 + (sx1 - sx0) * panel["cursor"][0]
        cy = sy0 + (sy1 - sy0) * panel["cursor"][1]
        k = min(w, h) * 0.05
        arrow = [(cx, cy), (cx, cy + k * 1.6), (cx + k * 0.42, cy + k * 1.2), (cx + k * 0.75, cy + k * 1.85),
                 (cx + k * 1.0, cy + k * 1.72), (cx + k * 0.68, cy + k * 1.08), (cx + k * 1.18, cy + k * 1.05)]
        d.polygon(arrow, fill=PAPER, outline=INK, width=4)
        if panel.get("circle", True):
            r = k * 1.6
            d.ellipse((cx - r * 0.6, cy - r * 0.5, cx + r * 1.4, cy + r * 1.5), outline=(220, 40, 40), width=8)
    return img


def head_info(img: Image.Image, framing: str) -> tuple[tuple[int, int, int, int], tuple[float, float, float]]:
    """キャラ全体の範囲と、頭の中心・幅・高さ（元画像の座標）。頭は上から何割かを頭として測る。"""
    a = np.asarray(img.getchannel("A")) > 20
    ys, xs = np.nonzero(a)
    if len(xs) == 0:
        return (0, 0, img.width, img.height), (img.width / 2, img.width / 2, img.height / 2)
    bx0, bx1, by0, by1 = xs.min(), xs.max(), ys.min(), ys.max()
    head_h = (by1 - by0) * (0.8 if framing == "bust" else 0.5)
    band = a[by0:int(by0 + head_h)]
    cols = np.nonzero(band.any(axis=0))[0]
    mass = band.sum(axis=0)
    hcx = float((np.arange(band.shape[1]) * mass).sum() / max(1, mass.sum()))
    head_w = float(cols.max() - cols.min()) if len(cols) else float(bx1 - bx0)
    return (int(bx0), int(by0), int(bx1), int(by1)), (hcx, head_w, float(head_h))


def frame_region(img: Image.Image, crop, aspect: float, framing: str) -> tuple[float, float, float, float]:
    """切り出す範囲。頭が必ず全部入るように、頭を基準に寄りの強さを決める。"""
    w, h = img.size
    if isinstance(crop, list):
        x0, y0, x1, y1 = crop[0] * w, crop[1] * h, crop[2] * w, crop[3] * h
        cw, ch = x1 - x0, y1 - y0
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if cw / ch < aspect:
            cw = ch * aspect
        else:
            ch = cw / aspect
        return cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2
    (bx0, by0, bx1, by1), (hcx, head_w, head_h) = head_info(img, framing)
    if crop == "full":
        vh = (by1 - by0) * 1.1
        cx = (bx0 + bx1) / 2
        vw = vh * aspect
        if vw < (bx1 - bx0) * 1.06:  # 横に広いポーズは幅で合わせる
            vw = (bx1 - bx0) * 1.06
            vh = vw / aspect
        top = by0 - vh * 0.05
        return cx - vw / 2, top, cx + vw / 2, top + vh
    vh = head_h * (1.5 if crop == "face" else 1.85)  # face でもあごまで入れる
    vw = vh * aspect
    if vw < head_w * 1.12:  # 縦長のコマでも頭の幅が入るように
        vw = head_w * 1.12
        vh = vw / aspect
    top = by0 - vh * 0.06
    return hcx - vw / 2, top, hcx + vw / 2, top + vh


def panel_background(panel: dict, size: tuple[int, int], monologue: bool) -> Image.Image:
    fx = panel.get("fx", [])
    if "flash" in fx:
        bg = Image.new("RGB", size, (255, 246, 206))
    elif "tone" in fx or monologue:
        bg = tone(size, dark=monologue and "light" not in fx)
    else:
        bg = Image.new("RGB", size, PAPER)
    bg = bg.convert("RGBA")
    if "lines" in fx or "flash" in fx:
        bg.alpha_composite(speed_lines(size, seed=len(panel.get("text", "")) + 3,
                                       color=(120, 100, 40) if "flash" in fx else INK))
    return bg


def panel_content(panel: dict, size: tuple[int, int], assets: dict, monologue: bool,
                  art: tuple[float, float, float, float] = (0, 0, 1, 1)) -> tuple[Image.Image, tuple[float, float]]:
    """コマの中身（背景効果＋キャラ）と、頭の位置（コマ内の 0〜1）。
    art はキャラを置く範囲（コマ内の 0〜1）。吹き出しの場所をあけてキャラを置くのに使う。
    寄せても粗くならないよう少し大きめに作る。"""
    pw, ph = int(size[0] * (1 + ZOOM)), int(size[1] * (1 + ZOOM))
    if panel["asset"] == "@screen":
        img = draw_screen((pw, ph), panel).convert("RGBA")
        if "lines" in panel.get("fx", []):
            img.alpha_composite(speed_lines((pw, ph), dense=60, seed=7))
        return img.convert("RGB"), (0.95, 0.5)
    bg = panel_background(panel, (pw, ph), monologue)
    ax0, ay0, ax1, ay1 = int(art[0] * pw), int(art[1] * ph), int(art[2] * pw), int(art[3] * ph)
    aw, ah = ax1 - ax0, ay1 - ay0
    img = source(panel["asset"], assets)
    meta = assets.get(panel["asset"], {})
    framing = meta.get("framing", "full")
    if meta.get("bg") == "scene":
        x0, y0, x1, y1 = frame_region(img, [0, 0, 1, 1], aw / ah, framing)
        head = (0.5, 0.35)
    else:
        x0, y0, x1, y1 = frame_region(img, panel.get("crop", "bust"), aw / ah, framing)
        _, (hcx, _, hh) = head_info(img, framing)
        hy = head_info(img, framing)[0][1] + hh * 0.8  # しっぽは口元に向ける
        head = ((ax0 + (hcx - x0) / (x1 - x0) * aw) / pw, (ay0 + (hy - y0) / (y1 - y0) * ah) / ph)
    scale = aw / (x1 - x0)
    resized = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    canvas.paste(resized, (int(ax0 - x0 * scale), int(ay0 - y0 * scale)), resized)
    if monologue and "light" not in panel.get("fx", []):
        dim = Image.new("RGBA", canvas.size, (30, 34, 60, 60))
        canvas = Image.alpha_composite(canvas, Image.composite(dim, Image.new("RGBA", canvas.size, (0, 0, 0, 0)), canvas.getchannel("A")))
    bg.alpha_composite(canvas)
    return bg.convert("RGB"), head


# ---------- ページ割り ----------

@dataclass
class Placed:
    panel: dict
    box: tuple[int, int, int, int]
    start: float
    seconds: float
    content: Image.Image | None = None
    frames: list | None = None  # 第3段階: 採用した Flow の動画のコマ
    overlay: Image.Image | None = None  # 吹き出し・擬音（ページ座標）
    monologue: bool = False
    key: str = ""
    placeholder: bool = False
    scene: dict = field(default_factory=dict)


@dataclass
class Page:
    scene: dict
    panels: list[Placed] = field(default_factory=list)
    start: float = 0.0
    seconds: float = 0.0


def layout(panels: list[dict]) -> list[tuple[int, int, int, int]]:
    """ページ内のコマを上から縦に積む（1ページ2コマまで。横に細いコマは作らない）。高さは "h"（比）で決める。"""
    weights = [p.get("h", 1.0) for p in panels]
    total_h = PAGE_BOTTOM - PAGE_TOP - GUTTER * (len(panels) - 1)
    boxes, y = [], PAGE_TOP
    for wgt in weights:
        rh = int(total_h * wgt / sum(weights))
        boxes.append((MARGIN, y, W - MARGIN, y + rh))
        y += rh + GUTTER
    return boxes


def paginate(panels: list) -> list[list]:
    """コマを2コマずつのページに分ける（シーンをまたいでよい。1コマだけ余るページを作らない）。
    "solo": true のコマは1コマで1ページ（見せ場）。"newPage": true のコマから新しいページにする。"""
    pages, cur = [], []
    for item in panels:
        p = item[1] if isinstance(item, tuple) else item
        if p.get("newPage") and cur:
            pages.append(cur)
            cur = []
        if p.get("solo"):
            if cur:
                pages.append(cur)
            pages.append([item])
            cur = []
            continue
        cur.append(item)
        if len(cur) == 2:
            pages.append(cur)
            cur = []
    if cur:
        pages.append(cur)
    return pages


# ---------- 吹き出し・擬音 ----------

def vertical_lines(text: str, per_col: int) -> list[str]:
    cols = []
    for part in text.translate(ZEN).split("\n"):
        while len(part) > per_col:
            cut = per_col
            # 句読点や小さい字は行頭に来ないよう前の列に寄せる
            while cut < len(part) and part[cut] in "、。！？…ーっゃゅょッャュョ）」":
                cut += 1
            cols.append(part[:cut])
            part = part[cut:]
        if part:
            cols.append(part)
    return cols


def balloon_size(panel: dict, box) -> tuple[float, float]:
    """吹き出しの半径（横・縦）。コマが低いときは1列の字数を減らして列を増やす。"""
    text = panel.get("text")
    if not text:
        return 0, 0
    size = panel.get("size", 52)
    room = (box[3] - box[1]) - 2 * size * 0.85 - 20
    per_col = min(panel.get("perCol", 9), max(4, int(room / (size * 1.02))))
    cols = vertical_lines(text, per_col)
    tw, th = int(size * 1.28) * len(cols), int(size * 1.02 * max(len(c) for c in cols))
    return tw * 0.5 + size * 0.95, th * 0.5 + size * 0.85


def balloon(overlay: Image.Image, box, panel: dict, monologue: bool, center, tgt) -> None:
    text = panel.get("text")
    if not text:
        return
    d = ImageDraw.Draw(overlay)
    size = panel.get("size", 52)
    f = vfont(size)
    room = (box[3] - box[1]) - 2 * size * 0.85 - 20
    cols = vertical_lines(text, min(panel.get("perCol", 9), max(4, int(room / (size * 1.02)))))
    col_w = int(size * 1.28)
    longest = max(len(c) for c in cols)
    tw, th = col_w * len(cols), int(size * 1.02 * longest)
    rx, ry = tw * 0.5 + size * 0.95, th * 0.5 + size * 0.85
    cx, cy = center
    ang = math.atan2(tgt[1] - cy, tgt[0] - cx)
    if monologue:
        fill = (250, 250, 255, 255)
        bumps = 18
        for k in range(bumps):
            a = 2 * math.pi * k / bumps
            ex, ey = cx + math.cos(a) * rx, cy + math.sin(a) * ry
            r = min(rx, ry) * 0.36
            d.ellipse((ex - r - 5, ey - r - 5, ex + r + 5, ey + r + 5), fill=INK + (255,))
        for k in range(bumps):
            a = 2 * math.pi * k / bumps
            ex, ey = cx + math.cos(a) * rx, cy + math.sin(a) * ry
            r = min(rx, ry) * 0.36
            d.ellipse((ex - r, ey - r, ex + r, ey + r), fill=fill)
        d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=fill)
        # 考えているときの小さな丸
        for j, rr in enumerate((22, 15, 10)):
            dist = 1.15 + 0.32 * j
            px, py = cx + math.cos(ang) * rx * dist, cy + math.sin(ang) * ry * dist
            d.ellipse((px - rr - 4, py - rr - 4, px + rr + 4, py + rr + 4), fill=INK + (255,))
            d.ellipse((px - rr, py - rr, px + rr, py + rr), fill=fill)
    else:
        fill = PAPER + (255,)
        shout = "shout" in panel.get("fx", [])
        if shout:
            # 叫びのギザギザ吹き出し
            pts = []
            n = 28
            for k in range(n):
                a = 2 * math.pi * k / n
                s = 1.18 if k % 2 == 0 else 0.92
                pts.append((cx + math.cos(a) * rx * s, cy + math.sin(a) * ry * s))
            d.polygon(pts, fill=fill, outline=INK + (255,), width=6)
        else:
            d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=fill, outline=INK + (255,), width=5)
        # しっぽ
        base = 0.22
        e1 = (cx + math.cos(ang - base) * rx * 0.9, cy + math.sin(ang - base) * ry * 0.9)
        e2 = (cx + math.cos(ang + base) * rx * 0.9, cy + math.sin(ang + base) * ry * 0.9)
        dist = math.hypot(tgt[0] - cx, tgt[1] - cy)
        reach = min(dist * 0.75, max(rx, ry) * 1.6)
        tip = (cx + math.cos(ang) * reach, cy + math.sin(ang) * reach)
        d.line([e1, tip, e2], fill=INK + (255,), width=5, joint="curve")
        d.polygon([e1, tip, e2], fill=fill)
        if not shout:
            d.arc((cx - rx, cy - ry, cx + rx, cy + ry), 0, 360, fill=INK + (255,), width=5)
            d.polygon([(cx + (e1[0] - cx) * 0.97, cy + (e1[1] - cy) * 0.97), tip,
                       (cx + (e2[0] - cx) * 0.97, cy + (e2[1] - cy) * 0.97)], fill=fill)
            d.line([e1, tip, e2], fill=INK + (255,), width=5, joint="curve")
    # 文字（縦書き、右の列から）
    x = cx + tw / 2 - col_w / 2 - size / 2
    for c in cols:
        d.text((x + (col_w - size) / 2, cy - th / 2), c, font=f, fill=INK, direction="ttb", features=["vert"])
        x -= col_w


def sfx(overlay: Image.Image, box, panel: dict, art=(0, 0, 1, 1), head=(0.5, 0.3)) -> None:
    """擬音。キャラを置く範囲の中で、頭と反対側の上に置く（吹き出しと顔にかからない）。"""
    s = panel.get("sfx")
    if not s:
        return
    text = s if isinstance(s, str) else s["text"]
    opts = {} if isinstance(s, str) else s
    size = opts.get("size", 96)
    f = vfont(size)
    bx0, by0, bx1, by1 = box
    if "pos" in opts:
        ax, ay = opts["pos"]
    else:
        # キャラを置く範囲の端（左右×上中下）から、顔からいちばん遠い所を選ぶ
        a0, ay0, a1, ay1 = art
        bw, bh = bx1 - bx0, by1 - by0
        spots = [(a0 + (a1 - a0) * fx, ay0 + (ay1 - ay0) * fy) for fx in (0.12, 0.88) for fy in (0.2, 0.5)]
        ax, ay = max(spots, key=lambda q: math.hypot((q[0] - head[0]) * bw, (q[1] - head[1]) * bh * 0.8))
    layer = Image.new("RGBA", (size * 2, int(size * 1.15 * len(text)) + size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((size // 2, size // 4), text.translate(ZEN), font=f, fill=tuple(opts.get("color", INK)), direction="ttb",
           features=["vert"], stroke_width=max(6, size // 10), stroke_fill=PAPER)
    layer = layer.rotate(opts.get("angle", 10), resample=Image.BICUBIC, expand=True)
    layer = layer.crop(layer.getbbox())
    limit = (by1 - by0) * 0.92
    if layer.height > limit:
        layer = layer.resize((int(layer.width * limit / layer.height), int(limit)), Image.LANCZOS)
    x = int(bx0 + (bx1 - bx0) * ax - layer.width / 2)
    y = int(by0 + (by1 - by0) * ay - layer.height / 2)
    # コマの内側に収める（次のコマに隠れないように）
    x = min(max(x, bx0 - 10), bx1 - layer.width + 10)
    y = min(max(y, by0 + 4), by1 - layer.height - 4)
    overlay.alpha_composite(layer, (max(0, x), max(0, y)))


# ---------- 組み立て ----------

def resolve(panel: dict) -> tuple[dict, bool]:
    """"need"（第2段階で作る絵）があればそれを使う。まだなければ "asset" を代役にする。"""
    need = panel.get("need")
    if not need:
        return panel, False
    if (ny.IMG_DIR / f"{need['id']}.png").exists():
        p = dict(panel, asset=need["id"])
        if "crop" in need:
            p["crop"] = need["crop"]
        return p, False
    return panel, True


def place_balloon(panel: dict, box) -> tuple[tuple[float, float, float, float], tuple[float, float] | None]:
    """吹き出しの置き場所と、キャラを置く範囲（コマ内の 0〜1）を決める。
    横長のコマは吹き出しを左、キャラを右に。縦長のコマは吹き出しを上、キャラを下に。顔に吹き出しがかからない。"""
    if not panel.get("text") or panel["asset"] == "@screen":
        if panel.get("text"):
            rx, ry = balloon_size(panel, box)
            return (0, 0, 1, 1), (box[0] + rx + 24, box[1] + ry + 24)
        return (0, 0, 1, 1), None
    x0, y0, x1, y1 = box
    pw, ph = x1 - x0, y1 - y0
    rx, ry = balloon_size(panel, box)
    side = panel.get("side") or ("left" if pw >= ph * 0.9 else "top")
    if side == "left":
        used = min(0.5, (2 * rx + 24) / pw)
        art = (used * 0.72, 0, 1, 1)
        center = (x0 + rx + 22, y0 + max(ry + 22, min(ph * 0.45, ph - ry - 10)))
    else:
        used = min(0.45, (2 * ry + 24) / ph)
        art = (0, used * 0.75, 1, 1)
        center = (x0 + pw * 0.34, y0 + ry + 22)
    return art, center


def clip_frames(clip: Path, size: tuple[int, int], seconds: float, rng=None) -> list[Image.Image]:
    """Flow の動画をコマの大きさに切り抜いて、表示する秒数ぶんのコマにする。音は使わない。"""
    a, b = rng or (0.0, seconds)
    w, h = size
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{a}", "-to", f"{b}", "-i", str(clip),
                        "-vf", f"setpts=PTS*{seconds / (b - a)},fps={FPS},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    n = len(r.stdout) // (w * h * 3)
    return [Image.frombytes("RGB", (w, h), r.stdout[i * w * h * 3:(i + 1) * w * h * 3]) for i in range(n)]


def build(episode: dict, characters: dict, assets_list: list) -> list[Page]:
    assets = {a["id"]: a for a in assets_list}
    pages, t = [], 0.0
    items = []
    for scene in episode["scenes"]:
        if not scene.get("panels"):
            sys.exit(f"シーン「{scene.get('line', '')[:12]}…」に panels がありません。")
        items += [(scene, p) for p in scene["panels"]]
    for chunk in paginate(items):
        page = Page(scene=chunk[0][0], start=t)
        boxes = layout([p for _, p in chunk])
        for j, ((scene, p), box) in enumerate(zip(chunk, boxes)):
            mono_scene = scene.get("type") == "monologue"
            p, placeholder = resolve(p)
            if not p["asset"].startswith("@") and p["asset"] not in assets and not (ny.IMG_DIR / f"{p['asset']}.png").exists():
                sys.exit(f"素材 {p['asset']} が assets/img か台帳にありません。")
            mono = p.get("monologue", mono_scene)
            size = (box[2] - box[0], box[3] - box[1])
            pl = Placed(p, box, t, float(p.get("seconds", 1.6)), monologue=mono,
                        key=f"p{len(pages) + 1:02d}-{j + 1}", placeholder=placeholder, scene=scene)
            art, center = place_balloon(p, box)
            pl.content, head = panel_content(p, size, assets, mono, art)
            clip = ny.CLIP_DIR / episode["id"] / f"{pl.key}.mp4"
            if p.get("flow") and clip.exists():
                if p["flow"].get("approved"):
                    pl.frames = clip_frames(clip, pl.content.size, pl.seconds, p["flow"].get("range"))
                else:
                    print(f"  {pl.key}: Flow の動画は未承認なので絵のまま（台本の flow に \"approved\": true で採用）")
            pl.overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sfx(pl.overlay, box, p, art, head)
            tgt = (box[0] + head[0] * size[0], box[1] + head[1] * size[1])
            if center:
                balloon(pl.overlay, box, p, mono, center, tgt)
            if placeholder:
                d = ImageDraw.Draw(pl.overlay)
                d.rounded_rectangle((box[0] + 14, box[3] - 62, box[0] + 150, box[3] - 14), radius=10, fill=(255, 236, 180, 235))
                d.text((box[0] + 82, box[3] - 38), "代役", font=ny.font(30), fill=(140, 90, 0), anchor="mm")
            page.panels.append(pl)
            t += pl.seconds
        page.seconds = t - page.start
        pages.append(page)
    return pages


def header(episode: dict, channel: str) -> Image.Image:
    img = Image.new("RGBA", (W, H), PAPER + (255,))
    ny.draw_header(img, episode, channel)
    return img


def draw_page(page: Page, t: float, base: Image.Image, final: bool = False) -> Image.Image:
    """時刻 t（ページ内の秒）のページ。final=True なら全コマを出した完成形。"""
    img = base.copy()
    d = ImageDraw.Draw(img)
    shown = []
    for pl in page.panels:
        local = (page.seconds if final else t) - (pl.start - page.start)
        if local < 0 and not final:
            continue
        x0, y0, x1, y1 = pl.box
        w, h = x1 - x0, y1 - y0
        progress = min(1.0, max(0.0, local / pl.seconds)) if not final else 1.0
        z = 1 + ZOOM * progress
        content = pl.content
        if pl.frames:
            content = pl.frames[min(len(pl.frames) - 1, max(0, int(local * FPS)))] if not final else pl.frames[-1]
        cw, ch = content.size
        vw, vh = w * (1 + ZOOM) / z, h * (1 + ZOOM) / z
        ox, oy = (cw - vw) / 2, (ch - vh) / 2
        if "shake" in pl.panel.get("fx", []) and 0 <= local < 0.6 and not final:
            ox += math.sin(local * 70) * 9
            oy += math.cos(local * 55) * 6
        view = content.crop((int(ox), int(oy), int(ox + vw), int(oy + vh))).resize((w, h), Image.BILINEAR)
        alpha = 1.0 if final else min(1.0, local / FADE)
        if alpha < 1:
            under = img.crop((x0, y0, x1, y1)).convert("RGB")
            view = Image.blend(under, view, alpha)
        img.paste(view, (x0, y0))
        d.rectangle((x0, y0, x1 - 1, y1 - 1), outline=INK, width=7)
        shown.append((pl, alpha))
    # 吹き出しと擬音はコマの絵より上に重ねる（後のコマに隠れないように）
    for pl, alpha in shown:
        if alpha >= 1:
            img.alpha_composite(pl.overlay)
        else:
            ov = pl.overlay.copy()
            ov.putalpha(ov.getchannel("A").point(lambda v: int(v * alpha)))
            img.alpha_composite(ov)
    return img


def label_page(img: Image.Image, page: Page) -> Image.Image:
    """コマ一覧用に、各コマへ番号・秒数・状態の札を付ける（動画には入らない）。"""
    img = img.copy()
    d = ImageDraw.Draw(img)
    f = ny.font(32)
    for pl in page.panels:
        x0, y0, x1, y1 = pl.box
        tags = [f"{pl.key}  {pl.seconds:.1f}秒"]
        if pl.panel.get("flow"):
            tags.append("Flow" + ("済" if pl.frames else "予定"))
        if pl.panel.get("solo"):
            tags.append("見せ場")
        txt = "  ".join(tags)
        tw = d.textlength(txt, font=f)
        d.rounded_rectangle((x1 - tw - 44, y0 + 10, x1 - 10, y0 + 58), radius=12, fill=(20, 20, 20, 210))
        d.text((x1 - 26, y0 + 34), txt, font=f, fill=(255, 255, 255), anchor="rm")
    return img


def sheet(episode_path: Path) -> Path:
    """各ページの完成形を並べたコマ一覧。北原さんの確認用。"""
    episode = ny.load_json(episode_path)
    characters = ny.load_json(ny.CHARACTERS)
    pages = build(episode, characters, ny.load_json(ny.LIBRARY)["assets"])
    base = header(episode, characters["channel"]["name"])
    tw, th = 480, 853
    thumbs = [label_page(draw_page(p, 0, base, final=True), p).convert("RGB").resize((tw, th), Image.LANCZOS) for p in pages]
    out = ny.OUT_DIR / episode["id"] / "manga" / "コマ一覧.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = min(4, len(thumbs))
    rows = -(-len(thumbs) // cols)
    gap, top, cap = 28, 150, 56
    sh = Image.new("RGB", (cols * (tw + gap) + gap, top + rows * (th + cap + gap)), (232, 232, 232))
    d = ImageDraw.Draw(sh)
    n = sum(len(p.panels) for p in pages)
    secs = sum(p.seconds for p in pages)
    stand = sum(pl.placeholder for p in pages for pl in p.panels)
    flows = sum(bool(pl.panel.get("flow")) for p in pages for pl in p.panels)
    d.text((gap, 24), f"{episode['id']} {episode['title']}　{len(pages)}ページ・{n}コマ・{secs:.1f}秒",
           font=ny.font(34), fill=(20, 20, 20))
    d.text((gap, 82), f"札: コマ番号と表示秒数／「代役」{stand}コマ＝Geminiの絵ができたら差し替え／「Flow予定」{flows}コマ＝前のコマから動かす",
           font=ny.font(26), fill=(70, 70, 70))
    for i, (im, p) in enumerate(zip(thumbs, pages)):
        x, y = gap + (i % cols) * (tw + gap), top + (i // cols) * (th + cap + gap)
        sh.paste(im, (x, y))
        d.rectangle((x - 1, y - 1, x + tw, y + th), outline=(150, 150, 150), width=2)
        d.text((x, y + th + 12), f"ページ{i + 1}　{len(p.panels)}コマ　{p.seconds:.1f}秒", font=ny.font(28), fill=(30, 30, 30))
    sh.save(out, quality=90)
    print(f"コマ一覧: {out.relative_to(ny.ROOT)}（{len(pages)}ページ、{n}コマ、{secs:.1f}秒）")
    return out


def render(episode_path: Path) -> Path:
    episode = ny.load_json(episode_path)
    characters = ny.load_json(ny.CHARACTERS)
    channel = characters["channel"]["name"]
    pages = build(episode, characters, ny.load_json(ny.LIBRARY)["assets"])
    base = header(episode, channel)
    end = ny.end_card(episode, channel).convert("RGBA")
    end_sec = float(episode.get("endCard", {}).get("seconds", 2.5))
    total = sum(p.seconds for p in pages) + end_sec
    out_dir = ny.OUT_DIR / episode["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / f"{episode['id']}_manga.mp4"
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v", "-map", "1:a", "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k", str(video)],
        stdin=subprocess.PIPE)
    finals = [draw_page(p, 0, base, final=True) for p in pages]
    for k in range(int(total * FPS)):
        t = k / FPS
        idx = next((i for i, p in enumerate(pages) if t < p.start + p.seconds), None)
        if idx is None:
            frame = end
            local_end = t - (total - end_sec)
            if local_end < TURN:
                frame = turn(finals[-1], end, local_end / TURN)
        else:
            p = pages[idx]
            frame = draw_page(p, t - p.start, base)
            if idx > 0 and t - p.start < TURN:
                frame = turn(finals[idx - 1], frame, (t - p.start) / TURN)
        proc.stdin.write(frame.convert("RGB").tobytes())
    proc.stdin.close()
    proc.wait()
    finals[0].convert("RGB").save(out_dir / "manga_preview.jpg", quality=88)
    print(f"完成: {video.relative_to(ny.ROOT)}  {ny.probe_seconds(video):.1f}秒")
    return video


def turn(prev: Image.Image, nxt: Image.Image, k: float) -> Image.Image:
    """ページをめくる（新しいページが左から入る。日本の漫画の向き）。"""
    k = 1 - (1 - k) ** 2
    img = prev.copy()
    x = int(-W + W * k)
    top = 0 if nxt.getpixel((5, 5))[:3] != prev.getpixel((5, 5))[:3] else 231  # ヘッダーは動かさない
    img.paste(nxt.crop((0, top, W, H)), (x, top))
    if x + W < W:
        ImageDraw.Draw(img).rectangle((x + W, top, x + W + 6, H), fill=(120, 120, 120, 255))
    return img


GEMINI_STEPS = [
    "1. Gemini アプリを開き、画像生成を選ぶ（プロトタイプ期は個人の Google AI Pro、運用期は弊社 Business Standard）。",
    "2. 「参照画像」のファイルを共有ドライブ `(PJ)未整理/追加　にゃーさん/` から添付する（最初の1枚がいちばん大事）。",
    "3. プロンプトをそのまま貼って生成する。やり直しは1枚につき3回まで。",
    "4. 採用した1枚を「保存名」で同じフォルダに保存する。Claude Code が取り込み、台帳登録とコマの差し替えをする。",
]


def prep(episode_path: Path) -> Path:
    """朝は生成ボタンを押すだけにするための準備: Gemini の画像生成指示書と、Flow のコマ画像＋依頼文。"""
    episode = ny.load_json(episode_path)
    characters = ny.load_json(ny.CHARACTERS)
    assets_list = ny.load_json(ny.LIBRARY)["assets"]
    assets = {a["id"]: a for a in assets_list}
    pages = build(episode, characters, assets_list)
    out_dir = ny.OUT_DIR / episode["id"] / "manga"
    flow_dir = out_dir / "flow"
    flow_dir.mkdir(parents=True, exist_ok=True)
    for old in flow_dir.glob("*.png"):  # コマ番号が変わったときに古いコマを残さない
        old.unlink()
    name = lambda aid: assets.get(aid, {}).get("driveName", f"{aid}.png")  # noqa: E731

    needs = [(pl, pl.panel["need"]) for pg in pages for pl in pg.panels if pl.placeholder]
    g = [f"# 画像の生成指示書 — {episode['id']} {episode['title']}", "",
         f"足りないコマの絵 {len(needs)} 枚。できるまでは手持ちの絵が「代役」として入っている。", "", "## 手順", *GEMINI_STEPS, ""]
    for pl, need in needs:
        ch = characters["characters"][need["character"]]
        refs = need.get("refs", []) + [pl.panel["asset"]] if pl.panel["asset"] not in need.get("refs", []) else need.get("refs", [])
        g += [f"## {need['id']}（{pl.key}、代役は {pl.panel['asset']}）", "",
              f"- キャラ: {ch['displayName']}",
              "- 参照画像: " + "、".join(f"`{name(r)}`" for r in refs),
              f"- 保存名: `{need['id']}.png`", "", "```",
              f"添付した1枚目の画像と同じキャラクター「{ch['displayName']}」を、同じ画風で1枚描いてください。",
              f"見た目: {ch['visual']}",
              f"描くもの: {need['desc']}",
              "条件: 顔・服装・髪型・色・主線の太さは参照画像と完全に同じ。正方形 1024x1024、無地の白背景、"
              "キャラ全体が画面に収まる。文字・ロゴ・効果線は入れない（吹き出しや集中線は後で入れる）。",
              "```", ""]
    gpath = out_dir / "画像の生成指示書.md"
    gpath.write_text("\n".join(g), encoding="utf-8")

    flows = [(pg, pl) for pg in pages for pl in pg.panels if pl.panel.get("flow")]
    f = [f"# Flow の指示書 — {episode['id']} {episode['title']}", "",
         "動かすのは、隣り合う2コマの間の小さな変化だけ。1本で1つの動きにする。", "",
         "## 手順（全クリップ共通）",
         "1. Flow で新しいプロジェクトを開き、エージェントの設定を「生成前に確認: 常時」、動画のデフォルトを Veo 3.1 - Fast・x1 にする。",
         "2. 下の「最初のコマ」「最後のコマ」の順に画像を添付する（順番が大事）。",
         "3. 依頼文をそのまま貼って送り、確認が出たら「承認」を押す（20クレジット）。",
         "4. できた動画をダウンロード（720p）し、「保存名」で共有ドライブに置く。採用はコマ確認シートを見て北原さんが決める。", "",
         f"枠の目安: {len(flows)} 本 × 20 = {len(flows) * 20} クレジット（やり直し込みで {int(len(flows) * 30)} 程度）。", ""]
    for pg, pl in flows:
        x0, y0, x1, y1 = pl.box
        wide = (x1 - x0) >= (y1 - y0)
        size = (1280, 720) if wide else (720, 1280)
        # Flow には効果線なしの白背景で、2枚とも同じ切り出し範囲（キャラの大きさと位置をそろえる）で渡す
        crop = pl.panel.get("crop", "bust")
        if isinstance(crop, str):
            crop = "full"  # Flow には全身で渡す（顔だけだと動きを想像できない）
            end_img = source(pl.panel["asset"], assets)
            bx = frame_region(end_img, crop, size[0] / size[1], assets.get(pl.panel["asset"], {}).get("framing", "full"))
            crop = [bx[0] / end_img.width, bx[1] / end_img.height, bx[2] / end_img.width, bx[3] / end_img.height]
        start = dict(pl.panel, asset=pl.panel["flow"]["from"], crop=pl.panel["flow"].get("fromCrop", crop), fx=[])
        end = dict(pl.panel, crop=crop, fx=[])
        for tag, panel in (("start", start), ("end", end)):
            img = panel_content(panel, (size[0], size[1]), assets, False)[0].resize(size, Image.LANCZOS)
            img.save(flow_dir / f"{pl.key}_{tag}.png")
        ch = characters["characters"][pl.panel.get("speaker", pl.scene["speaker"])]
        f += [f"## {pl.key}（{'横 16:9' if wide else '縦 9:16'}）→ 保存名 `{episode['id']}_{pl.key}.mp4`", "",
              f"- 最初のコマ: `flow/{pl.key}_start.png`（{start['asset']}）",
              f"- 最後のコマ: `flow/{pl.key}_end.png`（{pl.panel['asset']}）"
              + (f"　※代役の絵。`{pl.panel['need']['id']}` を生成してから `prep` をやり直し、そのあとで Flow に送る" if pl.placeholder else ""),
              "", "```",
              f"1枚目を最初のフレーム、2枚目を最後のフレームにして、Frames to Video で8秒・{'16:9' if wide else '9:16'}・音声なしの動画を1本作って。",
              "",
              f"2D anime chibi character, flat cel shading, thick black outlines. Keep the character design exactly as in the frames "
              f"({ch['visual'].replace('、白背景', '').replace('。白背景', '')}).",
              f"Motion: {pl.panel['flow']['motion']}",
              "The video starts exactly on the first image and ends exactly on the second image. "
              "The change happens within the first 2 seconds, then the character holds the second image still until the end. "
              "Never return to the first image.",
              "One continuous shot: fixed camera, no zoom, no cuts, the character stays the same size and position. "
              "Background stays exactly the same. No text, no new characters.",
              "Silent video: no voice, no music, no sound effects.",
              "```", ""]
    fpath = out_dir / "Flowの指示書.md"
    fpath.write_text("\n".join(f), encoding="utf-8")
    print(f"画像 {len(needs)} 枚 → {gpath.relative_to(ny.ROOT)}")
    print(f"Flow {len(flows)} 本 → {fpath.relative_to(ny.ROOT)}")
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["sheet", "prep", "render"])
    ap.add_argument("episode", type=Path)
    a = ap.parse_args()
    if a.command == "sheet":
        sheet(a.episode)
    elif a.command == "prep":
        prep(a.episode)
    else:
        render(a.episode)
    import policy  # 成果物を作るたびに芯と照らす（tools/policy.py）
    policy.run(a.episode)


if __name__ == "__main__":
    main()
