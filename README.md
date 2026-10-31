# DX事務所のにゃーさん — ショート自動制作（プロトタイプ）

PCが苦手な二代目「よちよち社長」と、少しだけ先を行く事務員「にゃーさん」のYouTubeショートを、
**定額プランの範囲だけで**毎日1本つくるための仕組み。

旧版（Gemini API＋Imagen を呼ぶ Flask デモ、Colab の Vertex AI 画像生成 v6.0）は従量課金だったため、
2026-10-28 の開発方針（Google は API 従量課金を使わない／Claude Code＋SKILL.md で作る）に合わせて作り直した。
旧版のコードは git 履歴（コミット 18af159）に残っている。

## 流れ

```
台本(JSON)  ──check──▶  素材の過不足 ──不足──▶ 画像の生成指示書.md ─▶ Geminiアプリで人が生成 ─▶ 台帳に登録
   │                                                                                  │
   └──────────────────────render──────────────────────────────────────────────────────┘
                              ▼
            縦型ショート mp4（1080x1920）＋ 投稿用テキスト ─▶ YouTube Studio で予約投稿（人）
```

| 工程 | 誰がやるか | 費用 |
|---|---|---|
| ネタ・台本 | Claude Code（`skills/nyasan-script`） | Claude の定額 |
| 素材選び・不足の洗い出し・生成プロンプト | `tools/nyasan.py check` | 0 |
| 足りない画像の生成 | 人が Gemini アプリで（`skills/nyasan-image-request`） | AI Pro／Business Standard の定額枠 |
| 見せ場の絵コンテ（最初と最後のコマ）と動きの指示 | `tools/nyasan.py storyboard` | 0 |
| 見せ場の動き（コマの間をつなぐ） | 人が Flow の Frames to Video で（1本2クリップまで） | 1クリップ20クレジット（定額枠） |
| 切り抜き・字幕・動き・書き出し | `tools/nyasan.py render`（Pillow＋ffmpeg） | 0 |
| 投稿 | 人が YouTube Studio で予約 | 0 |

## 使い方

```bash
pip install -r requirements.txt   # ffmpeg は別途
# 画像を assets/img/<素材ID>.png に置く（assets/library.json の driveId から取得）
python tools/nyasan.py check  episodes/EP001_mouse.json
python tools/nyasan.py storyboard episodes/EP001_mouse.json   # 動かすシーンがあるとき
python tools/nyasan.py render episodes/EP001_mouse.json
python -m pytest -q
```

## フォルダ

- `skills/` … Claude Code / Gemini Skills 共通の SKILL.md（1本作る・台本・画像の不足対応）
- `characters/characters.json` … キャラ設定とチャンネルの芯
- `assets/library.json` … 画像の台帳（正本は共有ドライブ。画像そのものはコミットしない）
- `episodes/` … 台本
- `tools/nyasan.py` … チェックと書き出し
- `out/` … 書き出し先（コミットしない）
