"""台本（episodes/<回>.json）の「直す所」だけを Google スプレッドシートに出し、直した数字を台本に戻す。

北原さんがスマホでシートの数字を直す → Claude が `pull` して書き出す、の形にするための道具。
2026-11-21 EP001 の v1→v11 の直しは全部「数字の変更」だったので、その列だけをシートに出す。

使い方:
  python tools/sheet.py push episodes/EP001_mouse.json [--folder <DriveフォルダID>]   # シートを作る／上書きする
  python tools/sheet.py pull episodes/EP001_mouse.json                                # シートの値を台本に戻す

鍵は環境変数 AVS_DRIVE_ORGANIZER_TOKEN_JSON（ファイルに書き出さない）。作ったシートの ID は台本の "sheet" に残す。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# シートの列。key はコマ（panel）の中の場所。読み取り専用の列は pull で無視する
COLUMNS = [
    ("コマ", None, False),
    ("絵", "asset", False),
    ("秒", "seconds", True),
    ("文字", "text", True),
    ("場面の文字", "caption", True),
    ("場面の文字の位置", "captionAt", True),   # tl/tr/bl/br（左上/右上/左下/右下）
    ("擬音", "sfx.text", True),
    ("動画 開始秒", "flow.range.0", True),
    ("動画 終了秒", "flow.range.1", True),
    ("動画の速さ", "flow.speed", True),
]
EPISODE_KEYS = [("ページの切り替え", "turn"), ("終わりの画面 秒", "endCard.seconds")]
NUMBER_KEYS = {"seconds", "flow.range.0", "flow.range.1", "flow.speed", "endCard.seconds"}


def panels(ep: dict):
    """コマ番号は manga.py と同じページ番号（p03-2 ＝ 3ページ目の2コマ目。クリップ名と同じ）。"""
    from manga import paginate
    items = [(sc, p) for sc in ep["scenes"] for p in sc["panels"]]
    for pi, chunk in enumerate(paginate(items)):
        for j, (_, p) in enumerate(chunk):
            yield f"p{pi + 1:02d}-{j + 1}", p


def get_path(d: dict, key: str):
    cur = d
    for part in key.split("."):
        if isinstance(cur, list):
            i = int(part)
            cur = cur[i] if i < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def set_path(d: dict, key: str, value) -> None:
    parts = key.split(".")
    cur = d
    for k, part in enumerate(parts[:-1]):
        nxt = parts[k + 1]
        if isinstance(cur, list):
            cur = cur[int(part)]
            continue
        if part not in cur or not isinstance(cur[part], (dict, list)):
            cur[part] = [0.0, 0.0] if nxt.isdigit() else {}
        cur = cur[part]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def to_cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(v)
    return str(v)


def from_cell(key: str, s: str):
    s = s.strip()
    if s == "":
        return None
    if key in NUMBER_KEYS:
        return float(s)
    return s.replace("\\n", "\n")


def to_rows(ep: dict) -> list[list[str]]:
    rows = [[c[0] for c in COLUMNS]]
    for pid, p in panels(ep):
        row = []
        for label, key, _ in COLUMNS:
            if key is None:
                row.append(pid)
            elif key == "asset":
                row.append((p.get("need") or {}).get("id") or p["asset"])
            else:
                v = get_path(p, key)
                row.append(to_cell(v))
        rows.append(row)
    rows.append([])
    for label, key in EPISODE_KEYS:
        rows.append([label, to_cell(get_path(ep, key))])
    return rows


def apply_rows(ep: dict, rows: list[list[str]]) -> list[str]:
    """シートの値を台本に戻す。変わった所を返す。"""
    changes = []
    head = rows[0]
    idx = {label: head.index(label) for label, _, _ in COLUMNS if label in head}
    byid = dict(panels(ep))
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        if r[0] in byid:
            p = byid[r[0]]
            for label, key, editable in COLUMNS:
                if not editable or label not in idx or idx[label] >= len(r):
                    continue
                new = from_cell(key, r[idx[label]])
                old = get_path(p, key)
                if new == old or (new is None and old in ("", None)):
                    continue
                if key.startswith("flow.") and "flow" not in p and new is None:
                    continue
                if new is None:
                    continue  # 空欄は「触らない」。消すときは台本で
                set_path(p, key, new)
                changes.append(f"{r[0]} {label}: {old} → {new}")
        else:
            for label, key in EPISODE_KEYS:
                if r[0] == label and len(r) > 1:
                    new = from_cell(key, r[1])
                    old = get_path(ep, key)
                    if new is not None and new != old:
                        set_path(ep, key, new)
                        changes.append(f"{label}: {old} → {new}")
    return changes


# ---------- Drive ----------

def token() -> str:
    t = json.loads(os.environ["AVS_DRIVE_ORGANIZER_TOKEN_JSON"])
    data = urllib.parse.urlencode({"client_id": t["client_id"], "client_secret": t["client_secret"],
                                   "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}).encode()
    return json.load(urllib.request.urlopen(t.get("token_uri", "https://oauth2.googleapis.com/token"), data))["access_token"]


def upload_sheet(at: str, name: str, csv_text: str, folder: str | None, file_id: str | None) -> str:
    meta = {"name": name, "mimeType": "application/vnd.google-apps.spreadsheet"}
    if folder and not file_id:
        meta["parents"] = [folder]
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
            f"--{b}\r\nContent-Type: text/csv; charset=UTF-8\r\n\r\n{csv_text}\r\n--{b}--").encode()
    if file_id:
        url = f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=multipart&supportsAllDrives=true"
        method = "PATCH"
    else:
        url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true"
        method = "POST"
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {at}", "Content-Type": f"multipart/related; boundary={b}"})
    return json.load(urllib.request.urlopen(req))["id"]


def download_csv(at: str, file_id: str) -> str:
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export?mimeType=text/csv"
    return urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {at}"})).read().decode("utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["push", "pull"])
    ap.add_argument("episode", type=Path)
    ap.add_argument("--folder", help="新しく作るときの Drive フォルダ ID")
    a = ap.parse_args()
    ep = json.loads(a.episode.read_text(encoding="utf-8"))
    at = token()
    if a.command == "push":
        buf = io.StringIO()
        csv.writer(buf).writerows(to_rows(ep))
        fid = upload_sheet(at, f"{ep['id']}_台本（直す所）", buf.getvalue(), a.folder, ep.get("sheet"))
        if ep.get("sheet") != fid:
            ep["sheet"] = fid
            a.episode.write_text(json.dumps(ep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"シート: https://docs.google.com/spreadsheets/d/{fid}/edit")
        return
    if not ep.get("sheet"):
        sys.exit("台本に sheet がありません。先に push してください")
    rows = list(csv.reader(io.StringIO(download_csv(at, ep["sheet"]))))
    changes = apply_rows(ep, rows)
    if changes:
        a.episode.write_text(json.dumps(ep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n".join(changes) if changes else "変更なし")


if __name__ == "__main__":
    main()
