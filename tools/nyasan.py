#!/usr/bin/env python3
"""にゃーさんショート自動生成ツール。

  python tools/nyasan.py check  episodes/EP001_mouse.json   # 素材の過不足を調べ、足りない画像の生成指示書を書く
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
OUT_DIR = ROOT / "out"

W, H, FPS = 1080, 1920, 30
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
]

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


def cutout_white(img: Image.Image, threshold: int = 238) -> Image.Image:
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
    return out.crop(bbox) if bbox else out


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
        d = ImageDraw.Draw(bgr)
        d.rounded_rectangle((W - 330, 250, W - 30, 320), radius=16, fill=(255, 240, 200, 230))
        d.text((W - 180, 285), f"代役: {pick.want.split('/')[1]}", font=font(30), fill=(140, 90, 0), anchor="mm")
    return bgr.convert("RGB")


def telop_layer(scene: dict, characters: dict, episode: dict, channel: str) -> Image.Image:
    """字幕・名札・ヘッダー（動かさない層）。"""
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
            telop_layer(scene, characters, episode, channel).save(over)
            clip = tmp / f"s{i:02d}.mp4"
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
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True).stdout.strip()
    print(f"\n完成: {video.relative_to(ROOT)}  {float(dur):.1f}秒")
    return video


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "render"])
    ap.add_argument("episode", type=Path)
    a = ap.parse_args()
    if a.command == "check":
        check(a.episode)
    else:
        render(a.episode)


if __name__ == "__main__":
    main()
