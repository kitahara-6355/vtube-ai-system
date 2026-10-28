---
name: nyasan-image-request
description: にゃーさん／社長の足りない表情・ポーズの画像を、定額枠のGeminiアプリで作って素材台帳に登録する。tools/nyasan.py check が「代役」を出したとき、または表情集を増やしたいときに使う。
---

# 足りないキャラ画像を作って登録する

画像生成は定額プランの Gemini アプリで行う。定額枠には API がないので、生成ボタンを押すのは人。それ以外（指示文づくり・台帳登録・動画への反映）は自動。

## どのアカウントで作るか
| 時期 | アカウント | 目安の枠 |
|---|---|---|
| プロトタイプ期（リリース前） | 個人の Google AI Pro | 画像 1日100枚前後（5時間ごと回復・週上限あり）、Flow 月1,000＋毎日50クレジット |
| リリース後の運用 | 弊社 Business Standard | Nano Banana Pro 月30枚、Flow 毎日50クレジット |

素材を使い回す設計なので、運用期の生成は1日0〜1枚の想定。表情集はプロトタイプ期に AI Pro でまとめて作る。

## 手順
1. `python tools/nyasan.py check episodes/<回>.json` を実行し、`out/<回>/画像の生成指示書.md` を開く。
2. 指示書の「参照画像」を共有ドライブから Gemini アプリに添付し、プロンプトを貼って生成する。
3. やり直しは最大3回。合わなければ台本の表情を手持ちの素材に変える（枠を守るため）。
4. 採用した1枚を「保存名」で共有ドライブ `(PJ)未整理/02[PJ] VTubeAIシステム/にゃーさん素材/` に保存する。個人アカウントで作った分も必ずここへ移す。
5. `assets/library.json` の `assets` に1行足す（`driveId` は保存後のファイルの ID）。
   ```json
   {"id": "SH_EXP_PANIC", "character": "shacho", "expression": "panic", "framing": "full", "bg": "white", "driveId": "…", "driveName": "SH_EXP_PANIC.png"}
   ```
6. 画像を `assets/img/SH_EXP_PANIC.png` に置き、もう一度 `check` して `OK` になることを確かめる。

## 品質の基準（採用するか決めるとき）
- 服・髪・色・主線の太さが参照画像と同じ（違う絵柄は `library.json` の `excluded` に理由付きで記録して使わない）
- 背景は無地の白（自動切り抜きのため）。影や床があると切り抜きが崩れる
- 文字やロゴが崩れて入っていない

## 優先して作る表情集（社長）
社長は設定画1枚しかないため、次の順で作る: panic（焦り）、joy（喜び）、troubled（困り）、proud（得意げ）、serious（キリッと、リアル寄り演出用）、embarrassed（照れ）。
