#!/usr/bin/env python3
"""にゃーさんショート自動生成ツール。

  python tools/nyasan.py check  episodes/EP001_mouse.json   # 素材の過不足を調べ、足りない画像の生成指示書を書く
  python tools/nyasan.py storyboard episodes/EP001_mouse.json  # 動かすシーンの絵コンテ（最初と最後のコマ）と Flow 用の指示書を作る
  python tools/nyasan.py review episodes/EP001_mouse.json     # Flow のクリップの確認シートを作る（採用は人が決めて clipApproved を書く）
  python tools/nyasan.py render episodes/EP001_mouse.json   # 縦型ショート(1080x1920)と投稿用テキストを作る

外部APIは呼ばない。必要なのは Python3 + Pillow + numpy + ffmpeg だけ。
画像は assets/img/<素材ID>.png に置く（正本は共有ドライブ。assets/library.json が台帳）。
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "assets" / "library.json"
CHARACTERS = ROOT / "characters" / "characters.json"
IMG_DIR = ROOT / "assets" / "img"
CLIP_DIR = ROOT / "assets" / "clips"
OUT_DIR = ROOT / "out"

W, H, FPS = 1080, 1920, 30
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
]

# Flow（Veo 3.1 Fast、最初と最後のフレーム指定）の1クリップあたりの消費。2026-10 時点の公開情報。
FLOW_CLIP_CREDITS = 20
FLOW_CLIP_SEC = 8

# 日本語の読み上げ・黙読の目安（文字/秒）。ショートは少し速めでよい。
CHARS_PER_SEC = 7.5
MIN_SCENE_SEC = 2.2


# ---------------------------------------------------------------- 台帳と素材選び

def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass
class Pick:
    asset: dict | None
    exact: bool
    want: str  # 欲しかった素材の説明（不足時の指示書に使う）


def pick_asset(assets: list[dict], character: str, expression: str | None, pose: str | None) -> Pick:
    """シーンに合う素材を選ぶ。完全一致がなければ同じキャラの代役を返し exact=False にする。"""
    expression = expression or "default"
    want = f"{character}/{expression}" + (f"/{pose}" if pose else "")
    own = [a for a in assets if a["character"] == character and not a.get("reference")]
    refs = [a for a in assets if a["character"] == character and a.get("reference")]

    def match(a: dict) -> bool:
        if a.get("expression", "default") != expression:
            return False
        return pose is None or a.get("pose") == pose

    exact = [a for a in own if match(a)]
    if exact:
        # 同じ条件ならバストアップ優先（ショートでは顔が大きいほうが伝わる）
        exact.sort(key=lambda a: {"bust": 0, "full": 1, "scene": 2}.get(a.get("framing"), 3))
        return Pick(exact[0], True, want)
    exact_ref = [a for a in refs if match(a)]
    if exact_ref:
        return Pick(exact_ref[0], True, want)

    fallback = [a for a in own if a.get("expression", "default") == "default" and a.get("bg") == "white"]
    fallback += [a for a in own + refs if a.get("bg") == "white"]
    return Pick(fallback[0] if fallback else None, False, want)


def scene_seconds(scene: dict) -> float:
    if "seconds" in scene:
        return float(scene["seconds"])
    n = len(scene.get("line", ""))
    sec = n / CHARS_PER_SEC + 0.8
    if scene.get("type") == "monologue":
        sec += 0.3
    return round(max(MIN_SCENE_SEC, sec), 2)


# ---------------------------------------------------------------- 生成指示書

def generation_request(missing: list[tuple[int, dict, Pick]], characters: dict, assets: list[dict], episode: dict) -> str:
    lines = [
        f"# 画像の生成指示書 — {episode['id']} {episode['title']}",
        "",
        "足りない素材だけを、定額枠の中で作るための指示書です（APIは使いません）。",
        "",
        "## 手順",
        "1. Gemini アプリを開き、画像生成を選ぶ。プロトタイプ期（リリース前）は個人の Google AI Pro、リリース後の運用は弊社 Business Standard アカウントを使う。",
        "2. 「参照画像」に書いたファイルを共有ドライブから添付する。",
        "3. 下のプロンプトをそのまま貼る。気に入るまで最大3回やり直す（4回目以降は表情を変えて台本側で吸収する）。",
        "4. 採用した1枚を「保存名」で共有ドライブ `(PJ)未整理/追加　にゃーさん/` に保存し、`assets/library.json` に1行足す。個人アカウントで作った場合も、採用分は必ず共有ドライブへ移す。",
        "",
        f"枠の目安: この回で {len({m[2].want for m in missing})} 種 × やり直し最大3回。AI Pro は1日100枚前後（5時間ごと回復・週上限あり）、Business Standard の Nano Banana Pro は月30枚。",
        "",
    ]
    seen: set[str] = set()
    for idx, scene, pick in missing:
        if pick.want in seen:
            continue
        seen.add(pick.want)
        ch_key, expression, *rest = pick.want.split("/")
        pose = rest[0] if rest else None
        ch = characters["characters"][ch_key]
        refs = [a for a in assets if a["character"] == ch_key and (a.get("reference") or a.get("expression") == "default")][:2]
        save_as = f"{'NY' if ch_key == 'nyasan' else 'SH'}_EXP_{expression.upper()}" + (f"_{pose.upper()}" if pose else "")
        mood = scene.get("direction") or scene.get("line", "")
        lines += [
            f"## {save_as}（シーン{idx + 1}で使用）",
            "",
            f"- キャラ: {ch['displayName']}",
            f"- 表情: {expression}" + (f" / ポーズ: {pose}" if pose else ""),
            f"- 参照画像: " + "、".join(f"`{r['driveName']}`" for r in refs),
            f"- 保存名: `{save_as}.png`",
            "",
            "```",
            f"添付した参照画像と同じキャラクター「{ch['displayName']}」を、同じ画風で1枚描いてください。",
            f"見た目: {ch['visual']}",
            f"今回の表情: {expression}。状況: 「{mood}」",
            "条件: 服装・髪型・色・主線の太さは参照画像と完全に同じ。正方形 1024x1024、無地の白背景、上半身〜全身が画面に収まる、文字やロゴを入れない。",
            "```",
            "",
        ]
    return "\n".join(lines)


def appeal_notes(episode: dict, characters: dict) -> list[str]:
    """この回の魅力と仕込みを表示する。キャラ設定にない言葉や欄の書き忘れは「要確認」にする。"""
    notes = []
    for key, text in episode.get("appeal", {}).items():
        appeal = characters["characters"][key]["appeal"]
        known = text in appeal["entry"] + appeal["deep"]
        notes.append(f"  魅力 {characters['characters'][key]['displayName']}: {text}{'' if known else '（要確認: キャラ設定にない言葉）'}")
    if not episode.get("appeal"):
        notes.append("  魅力: 要確認（appeal が未記入）")
    setup = episode.get("setup")
    threads = {t["id"] for t in characters["channel"]["storyThreads"]}
    if setup:
        notes.append(f"  仕込み {setup['thread']}: {setup['how']}{'' if setup['thread'] in threads else '（要確認: storyThreads にない id）'}")
    else:
        notes.append("  仕込み: 要確認（setup が未記入）")
    return notes


def check(episode_path: Path) -> tuple[list[tuple[int, dict, Pick]], Path | None]:
    episode = load_json(episode_path)
    assets = load_json(LIBRARY)["assets"]
    characters = load_json(CHARACTERS)
    missing, rows = [], []
    for i, scene in enumerate(episode["scenes"]):
        pick = pick_asset(assets, scene["speaker"], scene.get("expression"), scene.get("pose"))
        have = pick.asset and (IMG_DIR / f"{pick.asset['id']}.png").exists()
        status = "OK" if pick.exact else "代役"
        if not pick.exact:
            missing.append((i, scene, pick))
        rows.append(f"  {i + 1:>2}. {scene['speaker']:<7} {pick.want:<28} → {pick.asset['id'] if pick.asset else '-':<16} {status}{'' if have else ' (画像未取得)'}")
    print(f"{episode['id']} {episode['title']}  {sum(scene_seconds(s) for s in episode['scenes']) + episode.get('endCard', {}).get('seconds', 0):.1f}秒")
    print("\n".join(rows))
    for line in appeal_notes(episode, characters):
        print(line)
    req_path = None
    if missing:
        req_path = OUT_DIR / episode["id"] / "画像の生成指示書.md"
        req_path.parent.mkdir(parents=True, exist_ok=True)
        req_path.write_text(generation_request(missing, characters, assets, episode), encoding="utf-8")
        print(f"\n足りない素材 {len({m[2].want for m in missing})} 種 → {req_path.relative_to(ROOT)}")
    return missing, req_path


# ---------------------------------------------------------------- 描画

def font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    sys.exit("日本語フォントが見つかりません。FONT_CANDIDATES に追加してください。")


def cutout_white(img: Image.Image, threshold: int = 238, crop: bool = True) -> Image.Image:
    """白背景を外周から塗りつぶして透過にする。主線で囲まれた白（目のハイライト等）は残る。"""
    rgb = img.convert("RGB")
    arr = np.asarray(rgb).astype(np.int16)
    near_white = (arr.min(axis=2) >= threshold).astype(np.uint8) * 255
    mask = Image.fromarray(near_white, "L").copy()  # fromarray の画像は読み取り専用のことがあり floodfill が効かない
    w, h = mask.size
    step = 16
    seeds = [(x, 0) for x in range(0, w, step)] + [(x, h - 1) for x in range(0, w, step)]
    seeds += [(0, y) for y in range(0, h, step)] + [(w - 1, y) for y in range(0, h, step)]
    for s in seeds:
        if mask.getpixel(s) == 255:
            ImageDraw.floodfill(mask, s, 128)
    bg = (np.asarray(mask) == 128)
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    alpha_img = Image.fromarray(alpha, "L").filter(ImageFilter.GaussianBlur(1.2))
    out = rgb.convert("RGBA")
    out.putalpha(alpha_img)
    bbox = alpha_img.point(lambda v: 255 if v > 20 else 0).getbbox()
    return out.crop(bbox) if bbox and crop else out


def background(monologue: bool) -> Image.Image:
    """事務所の壁っぽい背景。背景素材ができたら差し替える。"""
    top, bottom = ((236, 241, 247), (214, 225, 238)) if not monologue else ((60, 70, 102), (34, 40, 64))
    grad = np.linspace(0, 1, H)[:, None]
    arr = np.zeros((H, W, 3), dtype=np.float32)
    for c in range(3):
        arr[:, :, c] = top[c] + (bottom[c] - top[c]) * grad
    img = Image.fromarray(arr.astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img)
    line = (255, 255, 255) if not monologue else (80, 92, 130)
    # 窓枠
    d.rounded_rectangle((90, 300, 990, 1080), radius=24, outline=line, width=10)
    d.line((540, 300, 540, 1080), fill=line, width=8)
    d.line((90, 690, 990, 690), fill=line, width=8)
    # 床
    d.rectangle((0, 1240, W, H), fill=(196, 170, 138) if not monologue else (44, 50, 76))
    return img


def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if f.getlength(cur + ch) > max_w and cur:
            # 句読点・閉じ括弧は行頭に来ないよう前の行に寄せる
            if ch in "、。！？」）…ー":
                cur += ch
                continue
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def draw_header(img: Image.Image, episode: dict, channel: str) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 230), fill=(25, 28, 40))
    d.text((W // 2, 70), channel, font=font(40), fill=(255, 205, 110), anchor="mm")
    title = episode["title"]
    f = font(64 if len(title) <= 14 else 52)
    d.text((W // 2, 160), title, font=f, fill=(255, 255, 255), anchor="mm")


def character_layer(scene: dict, pick: Pick, monologue: bool) -> Image.Image:
    """背景＋キャラ（ズームで少し動かす層）。"""
    bg = background(monologue)
    path = IMG_DIR / f"{pick.asset['id']}.png"
    src = Image.open(path)
    if pick.asset.get("bg") == "scene":
        # 背景込みの1枚絵は窓枠の位置に額装して使う
        pic = src.convert("RGB")
        pic.thumbnail((980, 980))
        x = (W - pic.width) // 2
        bg.paste(pic, (x, 270))
        return bg
    cut = cutout_white(src)
    framing = pick.asset.get("framing", "full")
    target_h = 1020 if framing == "bust" else 1060
    scale = target_h / cut.height
    cut = cut.resize((int(cut.width * scale), target_h), Image.LANCZOS)
    if cut.width > 1000:
        cut.thumbnail((1000, target_h))
    if monologue:
        # 心の声のときはキャラを少し暗く（考えている感じ）
        dim = Image.new("RGBA", cut.size, (20, 24, 50, 70))
        cut = Image.alpha_composite(cut, Image.composite(dim, Image.new("RGBA", cut.size, (0, 0, 0, 0)), cut.getchannel("A")))
    x = (W - cut.width) // 2
    y = 1300 - cut.height
    shadow = Image.new("RGBA", (cut.width, 60), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((cut.width * 0.2, 0, cut.width * 0.8, 50), fill=(0, 0, 0, 60))
    bgr = bg.convert("RGBA")
    bgr.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)), (x, 1270))
    bgr.alpha_composite(cut, (x, y))
    if not pick.exact:
        badge(bgr, f"代役: {pick.want.split('/')[1]}")
    return bgr.convert("RGB")


def badge(img: Image.Image, text: str, row: int = 0) -> None:
    """投稿してはいけない状態を右上の札で知らせる。"""
    d = ImageDraw.Draw(img)
    top = 250 + row * 90
    d.rounded_rectangle((W - 330, top, W - 30, top + 70), radius=16, fill=(255, 240, 200, 230))
    d.text((W - 180, top + 35), text, font=font(30), fill=(140, 90, 0), anchor="mm")


def telop_layer(scene: dict, characters: dict, episode: dict, channel: str, style: str = "box") -> Image.Image:
    """字幕・名札・ヘッダー（動かさない層）。style="bubble" で漫画の吹き出しにする。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_header(img, episode, channel)
    d = ImageDraw.Draw(img)
    ch = characters["characters"][scene["speaker"]]
    color = hex_rgb(ch["telopColor"])
    monologue = scene.get("type") == "monologue"
    text = scene["line"]
    if monologue and not text.startswith("（"):
        text = f"（{text}）"
    f = font(66)
    lines = wrap(text, f, 900)
    box_h = 90 + len(lines) * 92
    top = 1330
    if style == "bubble":
        draw_bubble(img, lines, f, color, monologue, ch["displayName"] + ("の心の声" if monologue else ""), top, box_h)
        return img
    if monologue:
        d.rounded_rectangle((40, top, W - 40, top + box_h), radius=36, fill=(18, 22, 48, 225), outline=(150, 170, 255, 255), width=6)
        fill = (235, 240, 255)
        tag = f"{ch['displayName']}の心の声"
    else:
        d.rounded_rectangle((40, top, W - 40, top + box_h), radius=36, fill=(255, 255, 255, 240), outline=color + (255,), width=8)
        fill = (30, 30, 30)
        tag = ch["displayName"]
    tf = font(40)
    tw = int(tf.getlength(tag)) + 60
    d.rounded_rectangle((70, top - 40, 70 + tw, top + 30), radius=20, fill=color + (255,))
    d.text((100, top - 5), tag, font=tf, fill=(255, 255, 255), anchor="lm")
    for i, ln in enumerate(lines):
        d.text((W // 2, top + 90 + i * 92), ln, font=f, fill=fill, anchor="mm")
    return img


def draw_bubble(img: Image.Image, lines: list[str], f: ImageFont.FreeTypeFont, color: tuple, monologue: bool,
                name: str, top: int, box_h: int) -> None:
    """漫画の吹き出し。セリフは角丸＋キャラへ向くしっぽ、心の声はもこもこ＋小さな丸のしっぽ。"""
    d = ImageDraw.Draw(img)
    left, right, bottom = 60, W - 60, top + box_h
    ink, paper = (40, 40, 40, 255), (255, 255, 255, 245)
    if monologue:
        paper = (238, 242, 255, 245)
        # もこもこの縁: 枠の周りに円を並べてから中を塗る
        r = 46
        for x in range(left + r, right - r + 1, 70):
            for y in (top + 8, bottom - 8):
                d.ellipse((x - r - 4, y - r - 4, x + r + 4, y + r + 4), fill=ink)
        for y in range(top + r, bottom - r + 1, 70):
            for x in (left + 8, right - 8):
                d.ellipse((x - r - 4, y - r - 4, x + r + 4, y + r + 4), fill=ink)
        for x in range(left + r, right - r + 1, 70):
            for y in (top + 8, bottom - 8):
                d.ellipse((x - r, y - r, x + r, y + r), fill=paper)
        for y in range(top + r, bottom - r + 1, 70):
            for x in (left + 8, right - 8):
                d.ellipse((x - r, y - r, x + r, y + r), fill=paper)
        d.rectangle((left + 8, top + 8, right - 8, bottom - 8), fill=paper)
        # 考えているときの小さな丸（キャラの頭の方へ）
        for cx, cy, rr in ((W // 2 + 40, top - 70, 26), (W // 2 + 80, top - 140, 18), (W // 2 + 110, top - 195, 12)):
            d.ellipse((cx - rr - 4, cy - rr - 4, cx + rr + 4, cy + rr + 4), fill=ink)
            d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=paper)
    else:
        # しっぽ（上のキャラの口元へ向ける）
        tail = [(W // 2 - 60, top + 10), (W // 2 + 60, top + 10), (W // 2 + 10, top - 110)]
        d.polygon(tail, fill=ink)
        d.rounded_rectangle((left, top, right, bottom), radius=60, fill=paper, outline=ink, width=8)
        inner = [(W // 2 - 46, top + 14), (W // 2 + 46, top + 14), (W // 2 + 10, top - 94)]
        d.polygon(inner, fill=paper)
    tf = font(36)
    tw = int(tf.getlength(name)) + 50
    d.rounded_rectangle((left + 30, bottom - 26, left + 30 + tw, bottom + 34), radius=18, fill=color + (255,))
    d.text((left + 55, bottom + 4), name, font=tf, fill=(255, 255, 255), anchor="lm")
    for i, ln in enumerate(lines):
        d.text((W // 2, top + 85 + i * 92), ln, font=f, fill=(30, 30, 30), anchor="mm")


def end_card(episode: dict, channel: str) -> Image.Image:
    card = episode.get("endCard", {})
    img = background(False).convert("RGBA")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, H), fill=(25, 28, 40, 215))
    d.text((W // 2, 760), card.get("message", "ゆっくり、いっしょに。"), font=font(70), fill=(255, 255, 255), anchor="mm")
    d.text((W // 2, 900), channel, font=font(56), fill=(255, 205, 110), anchor="mm")
    d.text((W // 2, 1040), card.get("cta", "チャンネル登録で、社長の成長を見守ってください"), font=font(40), fill=(220, 225, 240), anchor="mm")
    nya = IMG_DIR / "NY_EXP_JOY.png"
    if nya.exists():
        c = cutout_white(Image.open(nya))
        c.thumbnail((520, 520))
        img.alpha_composite(c, ((W - c.width) // 2, 1180))
    return img.convert("RGB")


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg 失敗:\n{' '.join(cmd)}\n{r.stderr[-2000:]}")


def render_clip(base: Path, overlay: Path | None, seconds: float, out: Path, zoom: bool = True) -> None:
    frames = int(round(seconds * FPS))
    z = "min(1+0.05*on/{n},1.05)".format(n=max(frames, 1)) if zoom else "1"
    vf = (
        f"[0:v]scale={W * 2}:{H * 2},zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
        f"trim=end_frame={frames},setpts=PTS-STARTPTS[bg]"
    )
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-i", str(base)]
    if overlay:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-i", str(overlay)]
        vf += f";[bg][1:v]overlay=0:0:shortest=1,fade=t=in:st=0:d=0.12[v]"
    else:
        vf += ";[bg]fade=t=in:st=0:d=0.2[v]"
    cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-filter_complex", vf, "-map", "[v]", "-map", f"{2 if overlay else 1}:a",
            "-t", f"{seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", str(out)]
    run(cmd)


def clip_path(episode: dict, index: int) -> Path:
    return CLIP_DIR / episode["id"] / f"scene{index + 1:02d}.mp4"


def probe_seconds(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def video_scene_seconds(text_seconds: float, clip_seconds: float) -> tuple[float, float]:
    """動きのクリップを入れるシーンの長さと再生速度。8秒の動きを最大2倍速まで縮め、足りなければシーンを延ばす。"""
    seconds = max(text_seconds, clip_seconds / 2)
    speed = max(1.0, clip_seconds / seconds)
    return round(seconds, 2), round(speed, 3)


def render_video_clip(video: Path, overlay: Path, text_seconds: float, out: Path,
                      clip_range: list[float] | None = None) -> float:
    """Flow で作った動きのクリップを土台にして字幕を重ねる。音は使わない（日本語のセリフは字幕で出す）。
    clip_range=[開始秒, 終了秒] で使う区間だけ切り出す（最後に元の絵へ戻るなど、崩れた部分を捨てる）。"""
    start, end = clip_range or (0.0, probe_seconds(video))
    seconds, speed = video_scene_seconds(text_seconds, end - start)
    vf = (
        f"[0:v]setpts=PTS/{speed},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},"
        f"tpad=stop_mode=clone:stop_duration={seconds},trim=duration={seconds},setpts=PTS-STARTPTS[bg];"
        f"[bg][1:v]overlay=0:0,fade=t=in:st=0:d=0.12[v]"
    )
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start}", "-to", f"{end}", "-i", str(video), "-loop", "1", "-framerate", str(FPS), "-i", str(overlay),
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-filter_complex", vf, "-map", "[v]", "-map", "2:a", "-t", f"{seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k", str(out)])
    return seconds


def storyboard(episode_path: Path) -> Path:
    """motion を書いたシーンだけ、最初と最後のコマ（9:16、字幕なし）と Flow 用の指示書を作る。"""
    episode = load_json(episode_path)
    characters = load_json(CHARACTERS)
    assets = load_json(LIBRARY)["assets"]
    out_dir = OUT_DIR / episode["id"] / "storyboard"
    out_dir.mkdir(parents=True, exist_ok=True)
    motion = [(i, s) for i, s in enumerate(episode["scenes"]) if s.get("motion")]
    lines = [
        f"# 絵コンテと動きの指示書 — {episode['id']} {episode['title']}",
        "",
        "動かすシーンだけ、Flow の「Frames to Video」で最初と最後のコマをつなぐ。それ以外のシーンは静止画＋ゆっくりズームのまま。",
        "",
        "## Flow の設定（全クリップ共通）",
        "- モード: Frames to Video（最初のフレーム＋最後のフレーム）",
        "- モデル: Veo 3.1 Fast / 縦 9:16 / 8秒",
        "- 音声なしで作る（Veo は音を付けることがあるので、書き出し時にも必ず消す）。セリフは字幕で出す",
        f"- 保存先: リポジトリの `assets/clips/{episode['id']}/sceneNN.mp4`（共有ドライブにも同じ名前で保存）",
        "",
        f"枠の目安: {len(motion)} クリップ × {FLOW_CLIP_CREDITS} クレジット = {len(motion) * FLOW_CLIP_CREDITS} クレジット（やり直し1回ずつで {len(motion) * FLOW_CLIP_CREDITS * 2}）。"
        "AI Pro は毎日50＋月1,000、Business Standard は毎日50。",
        "",
    ]
    for i, scene in enumerate(episode["scenes"]):
        if not scene.get("motion"):
            continue
        start = pick_asset(assets, scene["speaker"], scene.get("startExpression", scene.get("expression")), scene.get("startPose", scene.get("pose")))
        end = None
        if scene.get("endExpression") or scene.get("endPose"):
            end = pick_asset(assets, scene["speaker"], scene.get("endExpression", scene.get("expression")), scene.get("endPose"))
        for tag, pick in (("start", start), ("end", end)):
            if pick and pick.asset and (IMG_DIR / f"{pick.asset['id']}.png").exists():
                character_layer(scene, pick, False).save(out_dir / f"scene{i + 1:02d}_{tag}.png")
        ch = characters["characters"][scene["speaker"]]
        lines += [
            f"## シーン{i + 1}（{ch['displayName']}）→ `scene{i + 1:02d}.mp4`",
            "",
            f"- 最初のコマ: `scene{i + 1:02d}_start.png`（{start.asset['id']}{'' if start.exact else ' ※代役'}）",
            f"- 最後のコマ: " + (f"`scene{i + 1:02d}_end.png`（{end.asset['id']}{'' if end.exact else ' ※代役'}）" if end else "なし（最初のコマだけで動かす）"),
            f"- セリフ（字幕で出す）: {scene['line'].replace(chr(10), ' ')}",
            "",
            "Flow のエージェントに送る依頼文（画像を添付した順番が大事。最初のコマ→最後のコマの順に添付する）:",
            "",
            "```",
            (f"1枚目を最初のフレーム、2枚目を最後のフレームにして、Frames to Video で8秒・9:16・音声なしの動画を1本作って。"
             if end else "添付画像を最初のフレームにして、8秒・9:16・音声なしの動画を1本作って。"),
            "",
            f"2D anime chibi character, flat cel shading, thick black outlines. Keep the character design exactly as in the frames ({ch['visual'].replace('、白背景', '')}).",
            f"Motion: {scene['motion']}",
            ("The video starts exactly on the first image and ends exactly on the second image. "
             "Reach the pose of the second image by about 5 seconds and hold it still until the end. "
             "Never return to the first pose." if end else
             "The video starts exactly on the attached image. The motion finishes within about 5 seconds, "
             "then the character holds the final pose until the end. Never return to the starting pose."),
            "One continuous shot: fixed camera, no zoom, no cuts, the character stays the same size and position. "
            "Background stays exactly the same. No text, no subtitles, no new characters. Smooth, natural, light movement.",
            "Silent video: no voice, no music, no sound effects.",
            "```",
            "",
            "生成後: `python tools/nyasan.py review` で確認シートを作り、人が見て採用を決める（下の「確認のしかた」）。",
            "",
        ]
    lines += [
        "## 確認のしかた（採用は人が決める）",
        "",
        f"1. クリップを `assets/clips/{episode['id']}/sceneNN.mp4` に置き、`python tools/nyasan.py review episodes/<回>.json` を実行する。",
        f"2. `out/{episode['id']}/review/sceneNN_確認シート.jpg`（指定したコマと、0.5秒ごとのコマ一覧）を見て、次を確かめる。",
        "   - 絵柄（顔・服・タオル・主線）が指定したコマと同じ",
        "   - 途中でカメラが切り替わったり、キャラの大きさが急に変わったりしない",
        "   - 最後が「最後のコマ」の形で止まり、最初の絵に戻らない",
        "3. 良ければ台本のシーンに `\"clipApproved\": true` を書く。一部だけ使うときは `\"clipRange\": [開始秒, 終了秒]` も書く。",
        "   ダメなら作り直す（1シーン2回まで。それでもダメなら静止画のまま出す）。",
        "",
        "承認していないクリップを使った動画には右上に「未承認」の札が出る。札のある動画は投稿しない。",
        "",
    ]
    md = out_dir / "絵コンテ指示書.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"動かすシーン {len(motion)} 個 → {md.relative_to(ROOT)}")
    return md


def review(episode_path: Path) -> list[Path]:
    """Flow のクリップを人が判断するための確認シート（指定したコマ＋0.5秒ごとのコマ一覧）を作る。"""
    episode = load_json(episode_path)
    sb_dir = OUT_DIR / episode["id"] / "storyboard"
    out_dir = OUT_DIR / episode["id"] / "review"
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    for i, scene in enumerate(episode["scenes"]):
        video = clip_path(episode, i)
        if not video.exists():
            continue
        dur = probe_seconds(video)
        times = [round(t * 0.5, 1) for t in range(int(dur / 0.5))] + [round(dur - 0.05, 2)]
        cw, chh = 216, 384
        with tempfile.TemporaryDirectory() as tmp:
            frames = []
            for t in times:
                f = Path(tmp) / f"{t}.png"
                run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1",
                     "-vf", f"scale={cw}:{chh}", str(f)])
                frames.append((t, Image.open(f).convert("RGB")))
        cols = 6
        rows = -(-len(frames) // cols)
        sheet = Image.new("RGB", (cols * (cw + 12) + 12, 520 + rows * (chh + 56) + 20), (245, 245, 245))
        d = ImageDraw.Draw(sheet)
        d.text((16, 16), f"{episode['id']} シーン{i + 1} 確認シート（{dur:.1f}秒）  使う区間: {scene.get('clipRange', '全部')}  "
               f"承認: {'済' if scene.get('clipApproved') else 'まだ'}", font=font(26), fill=(30, 30, 30))
        x = 16
        for tag, label in (("start", "指定した最初のコマ"), ("end", "指定した最後のコマ")):
            p = sb_dir / f"scene{i + 1:02d}_{tag}.png"
            if p.exists():
                im = Image.open(p).convert("RGB")
                im.thumbnail((cw, chh))
                d.text((x, 64), label, font=font(22), fill=(30, 30, 30))
                sheet.paste(im, (x, 100))
                x += cw + 24
        d.text((x + 20, 100), "見るところ:\n・絵柄が指定のコマと同じ\n・途中でカメラが切り替わらない\n・キャラの大きさが急に変わらない\n・最後のコマで止まり、最初に戻らない",
               font=font(24), fill=(60, 60, 60), spacing=10)
        y0 = 520
        rng = scene.get("clipRange")
        for k, (t, im) in enumerate(frames):
            cx = 12 + (k % cols) * (cw + 12)
            cy = y0 + (k // cols) * (chh + 56)
            sheet.paste(im, (cx, cy))
            out = rng and not (rng[0] <= t <= rng[1])
            if out:
                d.rectangle((cx, cy, cx + cw, cy + chh), outline=(200, 40, 40), width=5)
            d.text((cx + 4, cy + chh + 8), f"{t:.1f}秒{'（切る）' if out else ''}", font=font(24), fill=(200, 40, 40) if out else (30, 30, 30))
        path = out_dir / f"scene{i + 1:02d}_確認シート.jpg"
        sheet.save(path, quality=88)
        sheets.append(path)
        print(f"シーン{i + 1}: {path.relative_to(ROOT)}")
    if not sheets:
        print(f"クリップがありません（assets/clips/{episode['id']}/sceneNN.mp4）")
    return sheets


def upload_text(episode: dict, channel: str, used_fallback: bool) -> str:
    up = episode.get("upload", {})
    tags = " ".join(up.get("hashtags", ["#Shorts", "#DX", "#中小企業"]))
    return "\n".join([
        "【YouTube Studio に貼るもの】",
        "",
        f"タイトル: {up.get('title', episode['title'])} {'#Shorts'}",
        "",
        "説明:",
        up.get("description", ""),
        "",
        tags,
        "",
        "設定メモ:",
        "- 公開: 予約投稿（毎日 19:00 を基本）",
        "- 「改変または合成されたコンテンツ」: キャラクター画像はAI生成のイラスト。実在の人物・出来事に見えないため、通常は「いいえ」でよい（判断に迷う回は「はい」）。",
        "- 投稿アカウント: 弊社 Business Standard アカウント",
        "",
        ("⚠ 代役の画像が入っています。生成指示書の画像を作って差し替えてから投稿してください。" if used_fallback else "代役なし。このまま投稿できます。"),
    ])


def render(episode_path: Path) -> Path:
    episode = load_json(episode_path)
    characters = load_json(CHARACTERS)
    assets = load_json(LIBRARY)["assets"]
    channel = characters["channel"]["name"]
    missing, _ = check(episode_path)
    out_dir = OUT_DIR / episode["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg が必要です。")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        clips = []
        for i, scene in enumerate(episode["scenes"]):
            pick = pick_asset(assets, scene["speaker"], scene.get("expression"), scene.get("pose"))
            if not pick.asset or not (IMG_DIR / f"{pick.asset['id']}.png").exists():
                sys.exit(f"シーン{i + 1}: 画像 {pick.asset['id'] if pick.asset else pick.want} が assets/img にありません。")
            mono = scene.get("type") == "monologue"
            base = tmp / f"s{i:02d}_base.png"
            over = tmp / f"s{i:02d}_over.png"
            character_layer(scene, pick, mono).save(base)
            overlay = telop_layer(scene, characters, episode, channel, characters["channel"].get("textStyle", "box"))
            moving = clip_path(episode, i)
            if moving.exists() and not scene.get("clipApproved"):
                print(f"  シーン{i + 1}: 動きのクリップは未承認（review で確認して clipApproved: true を書く）")
                badge(overlay, "未承認: 動き", row=1)
            overlay.save(over)
            clip = tmp / f"s{i:02d}.mp4"
            if moving.exists():
                render_video_clip(moving, over, scene_seconds(scene), clip, scene.get("clipRange"))
            else:
                if scene.get("motion"):
                    print(f"  シーン{i + 1}: 動きのクリップ {moving.relative_to(ROOT)} がないので静止画で書き出します")
                render_clip(base, over, scene_seconds(scene), clip)
            clips.append(clip)
            if i == 0:
                # 1枚目をサムネ確認用に残す
                Image.alpha_composite(Image.open(base).convert("RGBA"), Image.open(over)).convert("RGB").save(out_dir / "preview_first.jpg", quality=88)
        ec = tmp / "end.png"
        end_card(episode, channel).save(ec)
        clip = tmp / "end.mp4"
        render_clip(ec, None, float(episode.get("endCard", {}).get("seconds", 2.5)), clip, zoom=False)
        clips.append(clip)
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
        video = out_dir / f"{episode['id']}_short.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(video)])
    (out_dir / "投稿用テキスト.txt").write_text(upload_text(episode, channel, bool(missing)), encoding="utf-8")
    print(f"\n完成: {video.relative_to(ROOT)}  {probe_seconds(video):.1f}秒")
    return video


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "storyboard", "review", "render"])
    ap.add_argument("episode", type=Path)
    a = ap.parse_args()
    if a.command == "check":
        check(a.episode)
    elif a.command == "storyboard":
        storyboard(a.episode)
    elif a.command == "review":
        review(a.episode)
    else:
        render(a.episode)


if __name__ == "__main__":
    main()
