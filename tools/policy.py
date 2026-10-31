#!/usr/bin/env python3
"""にゃーさんチャンネルの方針ずれチェック（チャンネルの芯 = skills/nyasan-channel-core）。

  python tools/policy.py episodes/EP001_mouse.json   # 台本を芯と照らして out/<回>/方針チェック.md を書く
  python tools/policy.py --export                    # 芯のスキルを GWS 取り込み用の zip にまとめる

台本の文字から機械で分かるずれは「NG」「注意」で出す。画と動画のずれは機械では判定できないので、
同じ報告書に「目で見る項目」を並べ、スキルを呼んだ Claude か北原さんがコマ一覧を見て埋める。
nyasan.py check / manga.py sheet・prep・render からも毎回呼ばれる（投稿が少ないうちは常に確認する）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHARACTERS = ROOT / "characters" / "characters.json"
SKILL_DIR = ROOT / "skills" / "nyasan-channel-core"
OUT_DIR = ROOT / "out"

ONE_WORD = 10          # 一言セリフとみなす文字数（改行・記号は数えない）
MAX_ONE_WORDS = 2      # 1話に置ける一言セリフの数
OPENING_TEXT = 12      # 冒頭のコマに置ける文字数（冒頭2〜3秒は画で見せる）
LENGTH = (25, 35)      # 1話の秒数

# 紙・ハンコ・LINE を否定しない（芯 2）
HABITS = r"(紙|ハンコ|判子|手書き|FAX|ファックス|LINE|ライン|電話)"
DENY = r"(古い|時代遅れ|ダメ|だめ|無駄|ムダ|やめ|捨て|もう要らない|いらない|遅れて)"
# にゃーさんは専門家として教えない（芯 1）
EXPERT = ["常識", "簡単です", "簡単ですよ", "こんなことも", "知らないんですか", "当然", "基本中の基本", "初歩"]
# 社長を見下さない（あたたかい職場）
LOOK_DOWN = ["バカ", "ばか", "使えない", "ダメ社長", "情けない", "呆れ"]

VISUAL_ITEMS = [
    "冒頭2〜3秒で、選んだ入口の魅力が画だけで伝わるか（文字を読まなくても分かるか）",
    "キャラが見切れていないか。社長は青い作業着と首のタオル、にゃーさんは茶髪ボブと猫耳パーカーで描かれているか",
    "場面で起きていることが画だけで分かるか（状況説明を文字に頼りすぎていないか）",
    "にゃーさんの心の声と表の表情・態度にギャップがあるか（表はやさしい）",
    "職場があたたかく見えるか（社長を見下す、理不尽に見える画になっていないか）",
    "仕込み（setup）が画のどこかに、説明なしで置かれているか",
    "文字がショートの下のUI（下から480〜672px）と右のボタン列に隠れていないか",
    "Flow の動画: 途中ズームや表情の変化、最初の絵に戻る動きがないか（承認は北原さん）",
    "法人の担当者に見せても安心して紹介できるか（品位、教えの正確さ、売り込みが前に出ていないか）",
]


@dataclass
class Finding:
    level: str   # "NG" / "注意"
    rule: str
    detail: str


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def plain(text: str) -> str:
    return re.sub(r"[\s。、！？!?…・「」（）()ー〜]", "", text)


def texts(episode: dict) -> list[tuple[dict, dict | None, str, bool]]:
    """(シーン, コマ, 文字, 心の声か)。コマがない台本はシーンの line を使う。"""
    out = []
    for s in episode["scenes"]:
        mono = s.get("type") == "monologue"
        if s.get("panels"):
            out += [(s, p, p["text"], p.get("monologue", mono)) for p in s["panels"] if p.get("text")]
        elif s.get("line"):
            out.append((s, None, s["line"], mono))
    return out


def seconds(episode: dict) -> float:
    total = 0.0
    for s in episode["scenes"]:
        if s.get("panels"):
            total += sum(float(p.get("seconds", 1.6)) for p in s["panels"])
        else:
            total += max(2.0, len(plain(s.get("line", ""))) * 0.16 + 1.0)
    return total + episode.get("endCard", {}).get("seconds", 0)


def check(episode: dict, settings: dict) -> list[Finding]:
    chars, channel = settings["characters"], settings["channel"]
    f: list[Finding] = []

    # キャラの魅力と仕込み
    appeal = episode.get("appeal") or {}
    for key, c in chars.items():
        words = c["appeal"]["entry"] + c["appeal"]["deep"]
        if key not in appeal:
            f.append(Finding("NG", "この回の魅力", f"{c['displayName']} の魅力（appeal）が選ばれていません"))
        elif appeal[key] not in words:
            f.append(Finding("NG", "この回の魅力", f"{c['displayName']}「{appeal[key]}」はキャラ設定にない言葉です"))
    threads = {t["id"] for t in channel["storyThreads"]}
    setup = episode.get("setup")
    if not setup:
        f.append(Finding("NG", "仕込み", "仕込み（setup）がありません。毎話1つ、説明せずに置きます"))
    elif setup.get("thread") not in threads:
        f.append(Finding("NG", "仕込み", f"storyThreads にない id です: {setup.get('thread')}"))

    rows = texts(episode)
    # 冒頭は画で見せる（最初のコマの文字を数える）
    first = episode["scenes"][0]
    opening = (first["panels"][0].get("text") or "") if first.get("panels") else first.get("line", "")
    if len(plain(opening)) > OPENING_TEXT:
        f.append(Finding("注意", "冒頭2〜3秒", f"最初のコマの文字が{len(plain(opening))}字あります（{OPENING_TEXT}字以内で画を主役に）"))

    # 独白中心。セリフは一言だけ
    speech = [(s, p, t) for s, p, t, mono in rows if not mono]
    long_speech = [t for _, _, t in speech if len(plain(t)) > ONE_WORD]
    for t in long_speech:
        f.append(Finding("注意", "セリフは一言だけ", f"「{t.replace(chr(10), '')}」（{len(plain(t))}字）は心の声か画に置き換えられないか"))
    if len(speech) > MAX_ONE_WORDS:
        f.append(Finding("注意", "セリフは一言だけ", f"声に出すセリフが{len(speech)}個あります（1話{MAX_ONE_WORDS}個まで）"))
    monos = sum(1 for *_, mono in rows if mono)
    if monos < len(speech):
        f.append(Finding("注意", "モノローグが最重要", f"心の声 {monos} 個に対してセリフ {len(speech)} 個です"))

    # 締めはにゃーさんの心の声
    last = episode["scenes"][-1]
    if not (last["speaker"] == "nyasan" and last.get("type") == "monologue"):
        f.append(Finding("注意", "締め", "最後はにゃーさんの心の声で、やさしく締めます"))

    # 言葉のずれ
    for s, p, t, mono in rows:
        line = t.replace("\n", "")
        if re.search(HABITS + r".{0,8}" + DENY, line) or re.search(DENY + r".{0,4}" + HABITS, line):
            f.append(Finding("NG", "現状への尊重", f"「{line}」紙・ハンコ・LINE などを否定していないか"))
        if s["speaker"] == "nyasan":
            for w in EXPERT:
                if w in line:
                    f.append(Finding("NG", "少しだけ先", f"「{line}」の「{w}」は専門家が教える口ぶりです"))
        for w in LOOK_DOWN:
            if w in line:
                f.append(Finding("NG", "あたたかい職場", f"「{line}」の「{w}」は社長を見下して聞こえます"))

    # 尺
    sec = seconds(episode)
    if not LENGTH[0] <= sec <= LENGTH[1]:
        f.append(Finding("注意", "尺", f"{sec:.1f}秒です（{LENGTH[0]}〜{LENGTH[1]}秒）"))
    return f


def report(episode: dict, settings: dict, findings: list[Finding]) -> str:
    chars = settings["characters"]
    ng = sum(1 for x in findings if x.level == "NG")
    lines = [f"# 方針チェック {episode['id']} {episode['title']}", "",
             f"台本の文字から: NG {ng} / 注意 {len(findings) - ng}（芯は skills/nyasan-channel-core/SKILL.md）", ""]
    appeal = episode.get("appeal") or {}
    for key, text in appeal.items():
        if key in chars:
            lines.append(f"- この回の魅力 {chars[key]['displayName']}: {text}")
    if episode.get("setup"):
        lines.append(f"- 仕込み {episode['setup'].get('thread')}: {episode['setup'].get('how', '')}")
    lines += ["", "## 台本の文字から分かること", ""]
    lines += [f"- **{x.level}** {x.rule}: {x.detail}" for x in findings] or ["- ずれは見つかりませんでした"]
    lines += ["", "## 目で見て確かめること（コマ一覧・完成動画）", "",
              "スキルを呼んだ Claude がコマ一覧の画像を見て埋め、最後は北原さんが確認する。", ""]
    lines += [f"- [ ] {item}" for item in VISUAL_ITEMS]
    return "\n".join(lines) + "\n"


def run(episode_path: Path, quiet: bool = False) -> tuple[list[Finding], Path]:
    episode, settings = load(episode_path), load(CHARACTERS)
    findings = check(episode, settings)
    out = OUT_DIR / episode["id"] / "方針チェック.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report(episode, settings, findings), encoding="utf-8")
    ng = sum(1 for x in findings if x.level == "NG")
    if not quiet:
        print(f"方針チェック: NG {ng} / 注意 {len(findings) - ng} → {out.relative_to(ROOT)}")
        for x in findings:
            if x.level == "NG":
                print(f"  NG {x.rule}: {x.detail}")
    return findings, out


def export() -> Path:
    """GWS（Gemini の Skills）に取り込む zip。芯の正本は characters.json なので同梱する。"""
    out = OUT_DIR / "skills" / "nyasan-channel-core.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(SKILL_DIR / "SKILL.md", "nyasan-channel-core/SKILL.md")
        z.write(CHARACTERS, "nyasan-channel-core/references/characters.json")
    print(f"書き出し: {out.relative_to(ROOT)}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode", type=Path, nargs="?")
    ap.add_argument("--export", action="store_true")
    a = ap.parse_args()
    if a.export:
        export()
    elif a.episode:
        findings, _ = run(a.episode)
        sys.exit(1 if any(x.level == "NG" for x in findings) else 0)
    else:
        ap.error("台本のパスか --export を指定してください")


if __name__ == "__main__":
    main()
