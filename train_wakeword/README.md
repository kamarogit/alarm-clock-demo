# ウェイクワードモデル学習手順（Windows / RTX5080）

## 1. 環境準備

```powershell
# Python 3.11推奨
python -m venv venv
venv\Scripts\activate

# PyTorch（CUDA版）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128

# その他
pip install -r requirements.txt
```

## 2. サンプル音声生成

```powershell
python generate_samples.py
```

- `data/positive/` に「ねえクロード」「クロード」を7声×5速度で生成（約175件）
- `data/negative/` に他の日本語フレーズ＋ノイズを生成（約330件）

## 3. モデル学習

```powershell
python train.py
```

- RTX5080なら数分で完了
- `model/wakeword.onnx` が生成される

## 4. VMへ転送

```powershell
# Windows側で実行（VMのIPに合わせる）
scp model\wakeword.onnx kamaro@192.168.100.118:/home/kamaro/alarm-clock/models/wakeword.onnx
```

---

## ファイル構成

```
train_wakeword/
├── README.md             ← この手順書
├── requirements.txt      ← pip install -r で使う
├── generate_samples.py   ← Step2: サンプル生成
├── train.py              ← Step3: 学習 → wakeword.onnx
└── data/                 ← 生成後に作られる
    ├── positive/
    └── negative/
```

## モデルの精度が低い場合

- `NEGATIVE_PHRASES` により多くのフレーズを追加する
- `EPOCHS` を増やす（30 → 50）
- `generate_samples.py` の `WAKE_PHRASES` に失敗パターンを追加する
