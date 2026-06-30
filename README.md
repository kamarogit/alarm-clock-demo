# AI Talking Assistant

Dify + FastAPI (Python) + PWA 構成のAI音声アシスタント。
Windows PC / Raspberry Pi / Android のブラウザで動作する。

## 機能

| 機能 | 説明 |
|------|------|
| アラーム管理 | 複数アラーム、曜日繰り返し設定、スヌーズ |
| AI起床メッセージ | Difyが厳しさレベル・声スタイルに応じたメッセージを生成 |
| 朝のブリーフィング | 毎朝指定時刻に天気・ニュース・交通情報を読み上げ |
| 音声アシスタント | ウェイクワードで起動、Whisperで認識、Difyが回答 |
| TTS | edge-tts（Azure Neural音声）で高品質読み上げ |
| PWA | ホーム画面追加でフルスクリーン動作 |

## バックエンド

`.env` の `VOICE_BACKEND` で切り替え:

| 値 | 説明 |
|----|------|
| `dify` | Dify API（推奨） |
| `openrouter` | OpenRouter API経由 |
| `ollama` | ローカルLLM（Ollama） |
| `claude_cli` | Claude Code CLI（要ログイン済み環境） |

## セットアップ

→ [DEPLOY.md](DEPLOY.md) を参照

## Difyアプリの設定

Dify側のシステムプロンプトに以下を必ず追記すること:

```
【重要】会話が自然に終了したと判断した場合（ユーザーが「ありがとう」「じゃあね」
「バイバイ」「終わり」「もういい」などと言った場合）、
回答文の末尾に必ず「[END]」と付けてください。
通常の会話中は絶対に[END]を付けないでください。
```

## 音声アシスタントの動作

1. ブラウザが常時マイクを録音（2秒ごとにサーバーへ送信）
2. Whisper small (int8) でウェイクワード検出
3. ウェイクワードが検出されたらWeb Speech APIで質問を聞き取り
4. Difyが回答 → edge-ttsで読み上げ

## カスタマイズ

`voice_persona.md` を作成してアシスタントのキャラクター・ペルソナを設定する。

## 動作環境

| 環境 | 対応状況 |
|------|---------|
| Windows PC（Chrome/Edge） | ○（デモ推奨） |
| Raspberry Pi（Chromium） | ○ |
| Android（Fully Kiosk Browser） | ○ |

## ハードウェア要件

| リソース | 推奨値 | 備考 |
|---------|--------|------|
| vCPU | 4コア | Whisper推論で2コア消費 |
| RAM | 2GB | Whisper small で約1GB使用 |
| ストレージ | 10GB | Whisperモデル(462MB)含む |
| OS | Windows 10+ / Ubuntu 22.04+ / Raspberry Pi OS | |
