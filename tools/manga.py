#!/usr/bin/env python3
"""にゃーさんショートを「動く漫画」として書き出す（制作の第1段階）。

  python tools/manga.py sheet  episodes/EP001_mouse.json   # コマ一覧（ページごとの完成形）を画像で出す
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


def crop_box(img: Image.Image, crop, aspect: float, framing: str = "full") -> tuple[float, float, float, float]:
    """切り出す範囲（元画像の座標）。プリセット full/bust/face か [x0,y0,x1,y1]（0〜1）。コマの縦横比に広げる。
    framing="bust"（もともと胸から上の絵）は、bust を全体、face を顔まわりとして扱う。"""
    w, h = img.size
    if isinstance(crop, list):
        x0, y0, x1, y1 = crop[0] * w, crop[1] * h, crop[2] * w, crop[3] * h
    else:
        bb = img.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox() or (0, 0, w, h)
        bx0, by0, bx1, by1 = bb
        bw, bh = bx1 - bx0, by1 - by0
        cx = (bx0 + bx1) / 2
        if framing == "bust":
            crop = {"face": "bust_face", "bust": "full"}.get(crop, crop)
        if crop == "bust_face":
            x0, x1, y0, y1 = cx - bw * 0.42, cx + bw * 0.42, by0 + bh * 0.08, by0 + bh * 0.8
        elif crop == "face":
            x0, x1, y0, y1 = cx - bw * 0.42, cx + bw * 0.42, by0 - bh * 0.04, by0 + bh * 0.58
        elif crop == "bust":
            x0, x1, y0, y1 = cx - bw * 0.55, cx + bw * 0.55, by0 - bh * 0.05, by0 + bh * 0.66
        else:  # full
            x0, x1, y0, y1 = bx0 - bw * 0.08, bx1 + bw * 0.08, by0 - bh * 0.06, by1 + bh * 0.04
    cw, ch = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if cw / ch < aspect:
        cw = ch * aspect
    else:
        ch = cw / aspect
    return cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2


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


def panel_content(panel: dict, size: tuple[int, int], assets: dict, monologue: bool) -> Image.Image:
    """コマの中身（背景効果＋キャラ）。寄せても粗くならないよう少し大きめに作る。"""
    pw, ph = int(size[0] * (1 + ZOOM)), int(size[1] * (1 + ZOOM))
    fx = panel.get("fx", [])
    if "flash" in fx:
        bg = Image.new("RGB", (pw, ph), (255, 246, 206))
    elif "tone" in fx or monologue:
        bg = tone((pw, ph), dark=monologue and "light" not in fx)
    else:
        bg = Image.new("RGB", (pw, ph), PAPER)
    bg = bg.convert("RGBA")
    if "lines" in fx or "flash" in fx:
        bg.alpha_composite(speed_lines((pw, ph), seed=len(panel.get("text", "")) + 3,
                                       color=(120, 100, 40) if "flash" in fx else INK))
    img = source(panel["asset"], assets)
    x0, y0, x1, y1 = crop_box(img, panel.get("crop", "bust"), pw / ph, assets.get(panel["asset"], {}).get("framing", "full"))
    scale = pw / (x1 - x0)
    resized = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    canvas.paste(resized, (int(-x0 * scale), int(-y0 * scale)), resized)
    if monologue and "light" not in panel.get("fx", []):
        dim = Image.new("RGBA", canvas.size, (30, 34, 60, 60))
        canvas = Image.alpha_composite(canvas, Image.composite(dim, Image.new("RGBA", canvas.size, (0, 0, 0, 0)), canvas.getchannel("A")))
    bg.alpha_composite(canvas)
    return bg.convert("RGB")


# ---------- ページ割り ----------

@dataclass
class Placed:
    panel: dict
    box: tuple[int, int, int, int]
    start: float
    seconds: float
    content: Image.Image | None = None
    overlay: Image.Image | None = None  # 吹き出し・擬音（ページ座標）
    monologue: bool = False


@dataclass
class Page:
    scene: dict
    panels: list[Placed] = field(default_factory=list)
    start: float = 0.0
    seconds: float = 0.0


def layout(panels: list[dict]) -> list[tuple[int, int, int, int]]:
    """コマを行に並べる。同じ "row" のコマは横に並ぶ。行の高さは "h"（比）で決める。"""
    rows: list[list[int]] = []
    for i, p in enumerate(panels):
        if rows and p.get("row") is not None and panels[rows[-1][0]].get("row") == p.get("row"):
            rows[-1].append(i)
        else:
            rows.append([i])
    weights = [max(panels[i].get("h", 1.0) for i in r) for r in rows]
    total_h = PAGE_BOTTOM - PAGE_TOP - GUTTER * (len(rows) - 1)
    boxes: list = [None] * len(panels)
    y = PAGE_TOP
    for r, wgt in zip(rows, weights):
        rh = int(total_h * wgt / sum(weights))
        ws = [panels[i].get("w", 1.0) for i in r]
        total_w = W - 2 * MARGIN - GUTTER * (len(r) - 1)
        # 漫画は右から読むので、横並びは右から順に置く
        x = W - MARGIN
        for i, ww in zip(r, ws):
            cw = int(total_w * ww / sum(ws))
            boxes[i] = (x - cw, y, x, y + rh)
            x -= cw + GUTTER
        y += rh + GUTTER
    return boxes


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


def balloon(overlay: Image.Image, box, panel: dict, monologue: bool) -> None:
    text = panel.get("text")
    if not text:
        return
    d = ImageDraw.Draw(overlay)
    size = panel.get("size", 52)
    f = vfont(size)
    cols = vertical_lines(text, panel.get("perCol", 9))
    col_w = int(size * 1.28)
    longest = max(len(c) for c in cols)
    tw, th = col_w * len(cols), int(size * 1.02 * longest)
    bx0, by0, bx1, by1 = box
    ax, ay = panel.get("at", [0.8, 0.3])
    cx, cy = bx0 + (bx1 - bx0) * ax, by0 + (by1 - by0) * ay
    rx, ry = tw * 0.5 + size * 0.95, th * 0.5 + size * 0.85
    # はみ出しはページの内側に収める（コマの枠からは少しはみ出してよい）
    cx = min(max(cx, MARGIN + rx - 10), W - MARGIN - rx + 10)
    cy = min(max(cy, PAGE_TOP + ry - 30), PAGE_BOTTOM - ry + 30)
    tx, ty = panel.get("to", [0.45, 0.5])
    tgt = (bx0 + (bx1 - bx0) * tx, by0 + (by1 - by0) * ty)
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


def sfx(overlay: Image.Image, box, panel: dict) -> None:
    s = panel.get("sfx")
    if not s:
        return
    text = s if isinstance(s, str) else s["text"]
    opts = {} if isinstance(s, str) else s
    size = opts.get("size", 96)
    f = vfont(size)
    bx0, by0, bx1, by1 = box
    ax, ay = opts.get("at", [0.14, 0.2])
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

def build(episode: dict, characters: dict, assets_list: list) -> list[Page]:
    assets = {a["id"]: a for a in assets_list}
    pages, t = [], 0.0
    for scene in episode["scenes"]:
        panels = scene.get("panels")
        if not panels:
            sys.exit(f"シーン「{scene.get('line', '')[:12]}…」に panels がありません。")
        page = Page(scene=scene, start=t)
        boxes = layout(panels)
        mono_scene = scene.get("type") == "monologue"
        for p, box in zip(panels, boxes):
            if p["asset"] not in assets or not (ny.IMG_DIR / f"{p['asset']}.png").exists():
                sys.exit(f"素材 {p['asset']} が assets/img か台帳にありません。")
            mono = p.get("monologue", mono_scene)
            size = (box[2] - box[0], box[3] - box[1])
            pl = Placed(p, box, t, float(p.get("seconds", 1.6)), monologue=mono)
            pl.content = panel_content(p, size, assets, mono)
            pl.overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sfx(pl.overlay, box, p)
            balloon(pl.overlay, box, p, mono)
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
        cw, ch = pl.content.size
        vw, vh = w * (1 + ZOOM) / z, h * (1 + ZOOM) / z
        ox, oy = (cw - vw) / 2, (ch - vh) / 2
        if "shake" in pl.panel.get("fx", []) and 0 <= local < 0.6 and not final:
            ox += math.sin(local * 70) * 9
            oy += math.cos(local * 55) * 6
        view = pl.content.crop((int(ox), int(oy), int(ox + vw), int(oy + vh))).resize((w, h), Image.BILINEAR)
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


def sheet(episode_path: Path) -> Path:
    """各ページの完成形を並べたコマ一覧。北原さんの確認用。"""
    episode = ny.load_json(episode_path)
    characters = ny.load_json(ny.CHARACTERS)
    pages = build(episode, characters, ny.load_json(ny.LIBRARY)["assets"])
    base = header(episode, characters["channel"]["name"])
    thumbs = [draw_page(p, 0, base, final=True).convert("RGB").resize((360, 640), Image.LANCZOS) for p in pages]
    out = ny.OUT_DIR / episode["id"] / "manga" / "コマ一覧.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = min(3, len(thumbs))
    rows = -(-len(thumbs) // cols)
    sh = Image.new("RGB", (cols * 380 + 20, rows * 700 + 20), (235, 235, 235))
    d = ImageDraw.Draw(sh)
    for i, (im, p) in enumerate(zip(thumbs, pages)):
        x, y = 20 + (i % cols) * 380, 20 + (i // cols) * 700
        sh.paste(im, (x, y))
        d.text((x, y + 648), f"ページ{i + 1}  {len(p.panels)}コマ  {p.seconds:.1f}秒", font=ny.font(24), fill=(30, 30, 30))
    sh.save(out, quality=88)
    print(f"コマ一覧: {out.relative_to(ny.ROOT)}（{sum(len(p.panels) for p in pages)}コマ、{sum(p.seconds for p in pages):.1f}秒）")
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["sheet", "render"])
    ap.add_argument("episode", type=Path)
    a = ap.parse_args()
    if a.command == "sheet":
        sheet(a.episode)
    else:
        render(a.episode)


if __name__ == "__main__":
    main()
