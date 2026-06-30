# セットアップ手順

---

## Windows PC（デモ用・推奨）

### 前提

- Python 3.10 以上（[python.org](https://www.python.org/downloads/) からインストール）
- Git
- Chrome または Edge

### 1. リポジトリの取得

```powershell
git clone https://github.com/kamarogit/alarm-clock-demo.git
cd alarm-clock-demo
```

### 2. Python環境構築

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 環境変数設定

```powershell
copy .env.example .env
notepad .env
```

設定する項目:

| キー | 値 |
|-----|----|
| `VOICE_BACKEND` | `dify` |
| `DIFY_URL` | DifyサーバーのURL（例: `http://192.168.100.55`） |
| `DIFY_API_KEY` | DifyアプリのAPIキー（`app-xxxx`） |

### 4. SSL証明書生成（マイク使用に必須）

```powershell
openssl req -x509 -newkey rsa:4096 -nodes `
  -keyout key.pem -out cert.pem -days 3650 `
  -subj "/CN=localhost"
```

> openssl がない場合: [Git for Windows](https://git-scm.com/) に同梱されている。

### 5. Whisperモデルの事前ダウンロード

初回起動時の遅延を避けるために事前ダウンロード推奨（462MB）:

```powershell
python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); print('完了')"
```

### 6. 起動

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8001 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

### 7. ブラウザでアクセス

```
https://localhost:8001
```

証明書警告が出たら「詳細設定」→「localhost にアクセスする（安全ではありません）」をクリック。

マイクのアクセス許可を求めるダイアログが表示されたら「許可」を選択。

---

## Difyアプリの設定

Dify側のシステムプロンプトに以下を必ず追記:

```
あなたは音声アシスタントです。
ユーザーの質問に簡潔な日本語で回答してください。
3文以内でまとめ、自然な話し言葉で答えてください。
読み上げることを前提に、記号・箇条書き・URL・Markdownは絶対に使わず、文章のみで答えてください。

【重要】会話が自然に終了したと判断した場合（ユーザーが「ありがとう」「じゃあね」
「バイバイ」「終わり」「もういい」などと言った場合）、
回答文の末尾に必ず「[END]」と付けてください。
通常の会話中は絶対に[END]を付けないでください。
```

---

## Linux / Raspberry Pi（本番構成）

### 1. 依存パッケージ

```bash
sudo apt update && sudo apt install -y \
  git python3-pip python3-venv ffmpeg \
  build-essential libssl-dev
```

### 2. セットアップ

```bash
git clone https://github.com/kamarogit/alarm-clock-demo.git
cd alarm-clock-demo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env  # DIFY_URL と DIFY_API_KEY を設定
```

### 3. SSL証明書生成

```bash
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout key.pem -out cert.pem -days 3650 \
  -subj "/CN=alarm-clock"
```

### 4. Whisperモデルの事前ダウンロード

```bash
source venv/bin/activate
python3 -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); print('完了')"
```

### 5. systemdサービス登録

```bash
sudo tee /etc/systemd/system/alarm-clock.service > /dev/null << 'EOF'
[Unit]
Description=AI Talking Assistant
After=network.target

[Service]
Type=simple
User=kamaro
WorkingDirectory=/home/kamaro/alarm-clock-demo
ExecStart=/home/kamaro/alarm-clock-demo/venv/bin/uvicorn main:app \
  --host 0.0.0.0 --port 8001 \
  --ssl-keyfile key.pem --ssl-certfile cert.pem
Restart=on-failure
RestartSec=5
EnvironmentFile=/home/kamaro/alarm-clock-demo/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable alarm-clock
sudo systemctl start alarm-clock
```

---

## トラブルシューティング

**マイクが使えない**
→ HTTPSでないとブラウザがマイクを許可しない。SSL証明書の生成を確認。

**起動が遅い（初回）**
→ Whisperモデルのダウンロードが走っている。事前ダウンロード済みなら発生しない。

**Difyが応答しない**
→ `DIFY_URL` と `DIFY_API_KEY` を確認。DifyサーバーがLAN内から到達可能か確認。

**ウェイクワードが反応しない**
→ ログの `[Transcribe]` 行でWhisperが何を認識しているか確認する。

**ウェイクワードが暴発する**
→ 静かな環境での使用を前提としている。ノイズが多い環境では誤検知が増える。
