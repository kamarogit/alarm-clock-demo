# デプロイ手順

Ubuntu 24.04 VM → OPPO Reno7 A + Fully Kiosk Browser 構成への移行手順。

---

## 前提

- ホストマシン（KVM）で `new-vm.sh` が使えること
- 開発VM IP: 192.168.100.118
- 本番VM IP: デプロイ後に確認

---

## 1. VM作成

ホストマシン（192.168.100.11）で実行:

```bash
./new-vm.sh alarm-clock 2048 4 20
```

起動後にIPを確認（ARP自動検出）。以降 `<VM_IP>` はその値に置き換える。

---

## 2. VM初期設定

```bash
ssh kamaro@<VM_IP>

sudo apt update && sudo apt install -y \
  git python3-pip python3-venv ffmpeg \
  build-essential libssl-dev
```

---

## 3. コードのコピー

開発VM（192.168.100.118）で実行:

```bash
rsync -av \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='alarm.db' \
  --exclude='*.pem' \
  --exclude='voice_log.jsonl' \
  --exclude='static/briefing_latest.mp3' \
  --exclude='static/voice_response.mp3' \
  /home/kamaro/alarm-clock/ \
  kamaro@<VM_IP>:/home/kamaro/alarm-clock/
```

> `models/wakeword.onnx` と `models/wakeword.onnx.data` も一緒にコピーされる（現在は予備として保持）。

---

## 4. Python環境構築

本番VMで実行:

```bash
cd /home/kamaro/alarm-clock
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 5. 環境変数設定

```bash
cp .env.example .env
nano .env
```

最低限設定する項目:

| キー | 値 |
|-----|----|
| `VOICE_BACKEND` | `claude_cli`（Claude CLIを使う場合） |
| `OPENROUTER_API_KEY` | OpenRouter使用時のみ必要 |
| `ROOT_PATH` | nginxリバースプロキシ経由の場合（例: `/alarm-clock`） |

---

## 6. ペルソナのカスタマイズ

`voice_persona.md` を編集して使う人の情報に合わせる:

```bash
nano /home/kamaro/alarm-clock/voice_persona.md
```

記載する内容（例）:
- 名前・居住地・最寄り路線
- 家族構成
- 通常起床時間

> このファイルがアシスタントの回答精度（天気・交通情報の地域、呼びかけ方）に直結する。

---

## 7. SSL証明書生成

マイク使用にはHTTPSが必須（Fully KioskのSSL無視設定でも証明書ファイルが必要）:

```bash
cd /home/kamaro/alarm-clock

openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout key.pem -out cert.pem -days 3650 \
  -subj "/CN=alarm-clock"
```

---

## 8. Claude Code CLIのセットアップ

`VOICE_BACKEND=claude_cli` を使う場合に必須:

```bash
# Node.js インストール
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Claude Code CLIインストール
sudo npm install -g @anthropic-ai/claude-code

# ブラウザ認証（初回のみ・要インタラクティブ操作）
claude

# 動作確認
claude -p "こんにちは" --dangerously-skip-permissions
```

> 認証後は `~/.claude/` 以下にセッション情報が保存される。VM再作成時は再認証が必要。

---

## 9. Whisperモデルの事前ダウンロード

初回起動時の遅延を避けるために事前ダウンロード推奨（462MB）:

```bash
source /home/kamaro/alarm-clock/venv/bin/activate

python3 -c "
from faster_whisper import WhisperModel
print('ダウンロード中...')
WhisperModel('small', device='cpu', compute_type='int8')
print('完了')
"
```

---

## 10. systemdサービス登録

```bash
sudo tee /etc/systemd/system/alarm-clock.service > /dev/null << 'EOF'
[Unit]
Description=AI Alarm Clock
After=network.target

[Service]
Type=simple
User=kamaro
WorkingDirectory=/home/kamaro/alarm-clock
ExecStart=/home/kamaro/alarm-clock/venv/bin/uvicorn main:app \
  --host 0.0.0.0 --port 8001 \
  --ssl-keyfile key.pem --ssl-certfile cert.pem
Restart=on-failure
RestartSec=5
EnvironmentFile=/home/kamaro/alarm-clock/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable alarm-clock
sudo systemctl start alarm-clock
sudo systemctl status alarm-clock
```

---

## 11. 動作確認

```bash
# リアルタイムログ
sudo journalctl -u alarm-clock -f
```

ブラウザからアクセス:
```
https://<VM_IP>:8001
```
証明書警告が出たら「詳細設定」→「続行」で無視。

---

## 12. Fully Kiosk Browser設定（スマホ側）

| 設定項目 | 値 |
|---------|-----|
| スタートURL | `https://<VM_IP>:8001` |
| 画面オン保持 | ON |
| 画面の向き | 自動回転 |
| SSLエラーを無視 | ON |
| マイクアクセス | 許可 |
| テキスト読み上げ | 有効・日本語 |

---

## 13. nginx経由で外部公開（任意）

LAN内だけで使う場合は不要:

```bash
API_KEY=$(cat ~/.route-api-key)
curl -X POST http://192.168.100.50:8888/routes \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"alarm","path":"/alarm","target":"http://<VM_IP>:8001"}'
```

外部公開する場合、`.env` の `ROOT_PATH=/alarm` に設定する。

---

## トラブルシューティング

**マイクが使えない**
→ Fully KioskのPermission設定を確認。HTTPSでないと動作しない（証明書は自己署名でOK）。

**起動が遅い（初回）**
→ Whisperモデルのダウンロードが走っている。手順9で事前DL済みなら発生しない。

**`claude_cli` が応答しない**
```bash
claude -p "テスト" --dangerously-skip-permissions
```
で単体確認。エラーが出たら `claude` コマンドで再ログイン。

**レート制限エラー（Claude CLIの無料枠）**
→ 翌日リセットまで待つか、`.env` で `VOICE_BACKEND=openrouter` に切り替え。

**ウェイクワードが全く反応しない**
→ ログで `[Transcribe]` の結果を確認。Whisperが何を聞き取っているか確認できる。
```bash
sudo journalctl -u alarm-clock -f | grep Transcribe
```

**ウェイクワードが暴発する**
→ 静かな環境（寝室）での使用を前提としている。テレビ等のノイズがある環境では誤検知が増える。
