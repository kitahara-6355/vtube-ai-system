---
name: nyasan-script
description: 「DX事務所のにゃーさん」のショート台本を episodes/*.json の形で書く。社長とにゃーさんの掛け合い、心の声（モノローグ）中心、30秒前後。
---

# にゃーさんの台本を書く

## チャンネルの芯（毎回守る）
`characters/characters.json` の `channel.principles` を先に読む。要点は3つ。
1. にゃーさんは社長より「少しだけ先」。専門家として教えない。
2. 紙・ハンコ・LINE を否定しない。その上に一つ足す。
3. 心の声（モノローグ）のギャップが笑いと共感の源。

## 形
- 6シーン前後、合計25〜35秒（ツールが文字数から秒数を出す。1シーン最大28字くらいが読みやすい）
- 型: ①社長の宣言 → ②にゃーさんの心の声ツッコミ → ③社長がつまずく → ④にゃーさんが一言で助ける → ⑤社長の小さな成功 → ⑥にゃーさんの心の声で温かく締める
- 最後の心の声に、その回の「教え」を一つだけ入れる（例: ダブルクリックは少し速く2回）

## JSON の書き方

```json
{
  "id": "EP002",
  "title": "15字以内のタイトル",
  "story": "ネタ帳のID や元ネタ",
  "funnelStage": 1,
  "scenes": [
    {"speaker": "shacho", "type": "line", "expression": "default", "line": "セリフ"},
    {"speaker": "nyasan", "type": "monologue", "expression": "worry", "line": "心の声（括弧は自動で付く）"},
    {"speaker": "nyasan", "type": "line", "pose": "point", "line": "セリフ"},
    {"speaker": "shacho", "type": "line", "expression": "joy", "direction": "画像を作るときの演出メモ", "line": "セリフ"}
  ],
  "endCard": {"seconds": 2.5, "message": "ゆっくり、いっしょに。", "cta": "チャンネル登録で、社長の成長を見守ってください"},
  "upload": {"title": "…【DX事務所のにゃーさん】", "description": "…", "hashtags": ["#Shorts", "#DX", "#中小企業"]}
}
```

- `speaker`: `shacho` / `nyasan`
- `type`: `line`（セリフ）/ `monologue`（心の声。背景が暗くなる）
- `expression` と `pose` は `assets/library.json` にある値を優先して使う。ない値を書くと「代役」になり、生成指示書が出る。
  - にゃーさんの表情: default, joy, worry, troubled, cheer, shy ／ ポーズ: point, bow, docs, tea, pc, explain, nod, wave, greet
  - 社長: 今は default だけ（表情集を作るまでは代役が出る）
- `direction`: 新しい画像が要るシーンだけ書く。生成指示書の「状況」に入る。
- 字幕の折り返しを指定したいときは `line` に `\n` を入れる。

## 書いたら
`python tools/nyasan.py check episodes/<ファイル>.json` で秒数と素材を確認する。
