# AI Alarm Clock

OPPO Reno7 A + Fully Kiosk Browser 向けのスマートアラームクロック。
FastAPI (Python) + PWA 構成でローカルLAN上で動作する。

## 機能

| 機能 | 説明 |
|------|------|
| アラーム管理 | 複数アラーム、曜日繰り返し設定、スヌーズ |
| AI起床メッセージ | Claude CLIが厳しさレベル・声スタイルに応じたメッセージを生成 |
| 朝のブリーフィング | 毎朝指定時刻に天気・ニュース・交通情報を読み上げ（読み上げ後5秒で自動クローズ） |
| 音声アシスタント | 「ねえクロード」で起動、Whisperで認識、Claude CLIが回答 |
| TTS | edge-tts（Azure Neural音声）で高品質読み上げ |
| PWA | ホーム画面追加でフルスクリーン動作 |

## 構成

```
alarm-clock/
├── main.py              # FastAPI エントリポイント
├── scheduler.py         # APScheduler アラーム管理
├── voice.py             # 音声アシスタント（Claude CLI連携）
├── whisper_stt.py       # Whisper音声認識（ウェイクワード検出）
├── wakeword.py          # カスタムウェイクワード推論（現在は予備）
├── briefing.py          # 朝のブリーフィング生成
├── database.py          # SQLAlchemy + SQLite
├── models.py            # DBモデル定義
├── voice_persona.md     # アシスタントのペルソナ設定（★要カスタマイズ）
├── briefing.json        # ブリーフィング設定（enabled/time）
├── .env                 # 環境変数（.env.exampleを参照）
├── models/              # wakeword.onnx（カスタムモデル・予備）
├── static/              # PWA manifest, service worker, sounds
├── templates/           # index.html（PWAフロントエンド）
└── train_wakeword/      # カスタムウェイクワードモデル学習スクリプト
    └── IMPROVE.md       # モデル精度改善の検討
```

## バックエンド選択

`.env` の `VOICE_BACKEND` で切り替え:

| 値 | 説明 |
|----|------|
| `claude_cli` | Claude Code CLIを使用（要ログイン、ウェブ検索可） |
| `openrouter` | OpenRouter API経由（API Key要、ウェブ検索不可） |
| `ollama` | ローカルLLM（Ollama、ウェブ検索不可） |

## セットアップ

→ [DEPLOY.md](DEPLOY.md) を参照

## 音声アシスタントの動作

1. ブラウザが常時マイクを録音（2秒ごとにサーバーへ送信）
2. Whisper small (int8) でウェイクワード検出
3. 「ねえクロード」系が検出されたらWeb Speech APIで質問を聞き取り
4. Claude CLIが回答 → edge-ttsで読み上げ

## カスタマイズ

`voice_persona.md` を編集することで別の人向けに設定変更できる（名前・居住地・路線など）。

## ハードウェア要件（本番実績）

| リソース | 推奨値 | 備考 |
|---------|--------|------|
| vCPU | 4コア | Whisper推論で2コア消費 |
| RAM | 2GB | Whisper small で約1GB使用 |
| ストレージ | 20GB | Whisperモデル(462MB)含む |
| OS | Ubuntu 24.04 | |
