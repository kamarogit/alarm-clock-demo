"""
ウェイクワード検出モデルの学習スクリプト
PyTorch (GPU) + MFCC特徴量 + 小型CNN

実行: python train.py
出力: model/wakeword.onnx  ← これをVMに転送する
"""

import os
import numpy as np
import librosa
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from pathlib import Path
from sklearn.model_selection import train_test_split
import onnx

# ── 設定 ────────────────────────────────────────────────────────────────────

POSITIVE_DIR = Path("data/positive")
NEGATIVE_DIR = Path("data/negative")
MODEL_DIR = Path("model")
MODEL_PATH = MODEL_DIR / "wakeword.onnx"

SR = 16000
DURATION = 2.0        # 秒（VMの録音セグメントに合わせる）
N_MFCC = 40
N_FRAMES = 87         # 2秒 / hopsize

BATCH_SIZE = 32
EPOCHS = 30
LR = 1e-3
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


# ── 特徴量抽出 ───────────────────────────────────────────────────────────────

def extract_features(path: str) -> np.ndarray | None:
    try:
        y, _ = librosa.load(path, sr=SR, duration=DURATION, mono=True)
        if len(y) < SR * 0.3:
            return None
        # ゼロパディングで長さを統一
        target = int(SR * DURATION)
        if len(y) < target:
            y = np.pad(y, (0, target - len(y)))
        else:
            y = y[:target]
        mfcc = librosa.feature.mfcc(y=y, sr=SR, n_mfcc=N_MFCC)
        return mfcc.astype(np.float32)   # (40, 87)
    except Exception as e:
        print(f"  特徴量抽出失敗: {path}: {e}")
        return None


# ── Dataset ──────────────────────────────────────────────────────────────────

class WakeWordDataset(Dataset):
    def __init__(self, features, labels):
        self.X = torch.tensor(features, dtype=torch.float32).unsqueeze(1)  # (N,1,40,87)
        self.y = torch.tensor(labels, dtype=torch.long)

    def __len__(self):
        return len(self.y)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]


# ── モデル（小型CNN） ─────────────────────────────────────────────────────────

class WakeWordCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, kernel_size=3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(64, 128, kernel_size=3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d((4, 4)),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * 4 * 4, 256),
            nn.ReLU(),
            nn.Dropout(0.5),
            nn.Linear(256, 2),
        )

    def forward(self, x):
        return self.classifier(self.features(x))


# ── 学習 ─────────────────────────────────────────────────────────────────────

def main():
    print(f"デバイス: {DEVICE}")
    MODEL_DIR.mkdir(exist_ok=True)

    # データ読み込み
    print("\n=== 特徴量抽出中 ===")
    X, y = [], []

    pos_files = list(POSITIVE_DIR.glob("*"))
    neg_files = list(NEGATIVE_DIR.glob("*"))
    print(f"ポジティブ: {len(pos_files)}件, ネガティブ: {len(neg_files)}件")

    for path in pos_files:
        feat = extract_features(str(path))
        if feat is not None:
            X.append(feat)
            y.append(1)

    for path in neg_files:
        feat = extract_features(str(path))
        if feat is not None:
            X.append(feat)
            y.append(0)

    X = np.array(X)
    y = np.array(y)
    print(f"有効データ: {len(y)}件 (正例:{y.sum()}, 負例:{(y==0).sum()})")

    # 学習・検証分割
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    train_ds = WakeWordDataset(X_train, y_train)
    val_ds   = WakeWordDataset(X_val,   y_val)
    train_dl = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
    val_dl   = DataLoader(val_ds,   batch_size=BATCH_SIZE)

    # モデル学習
    print("\n=== 学習開始 ===")
    model = WakeWordCNN().to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    criterion = nn.CrossEntropyLoss()
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, EPOCHS)

    best_val_acc = 0
    for epoch in range(1, EPOCHS + 1):
        model.train()
        losses = []
        for xb, yb in train_dl:
            xb, yb = xb.to(DEVICE), yb.to(DEVICE)
            optimizer.zero_grad()
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()
            losses.append(loss.item())
        scheduler.step()

        model.eval()
        correct = total = 0
        with torch.no_grad():
            for xb, yb in val_dl:
                xb, yb = xb.to(DEVICE), yb.to(DEVICE)
                preds = model(xb).argmax(1)
                correct += (preds == yb).sum().item()
                total += len(yb)
        val_acc = correct / total
        print(f"Epoch {epoch:2d}/{EPOCHS}  loss={np.mean(losses):.4f}  val_acc={val_acc:.3f}")

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), MODEL_DIR / "best.pt")

    # ONNX エクスポート
    print(f"\n=== ONNXエクスポート (best val_acc={best_val_acc:.3f}) ===")
    model.load_state_dict(torch.load(MODEL_DIR / "best.pt", map_location=DEVICE))
    model.eval()

    dummy = torch.zeros(1, 1, N_MFCC, N_FRAMES).to(DEVICE)
    torch.onnx.export(
        model, dummy, str(MODEL_PATH),
        input_names=["mfcc"],
        output_names=["logits"],
        dynamic_axes={"mfcc": {0: "batch"}},
        opset_version=17,
    )
    # 外部データファイルがある場合は単一ファイルに統合
    import onnx
    onnx_model = onnx.load(str(MODEL_PATH), load_external_data=True)
    onnx.save(onnx_model, str(MODEL_PATH), save_as_external_data=False)
    print(f"保存: {MODEL_PATH}")
    print("\n完了！ model/wakeword.onnx をVMの alarm-clock/models/ にコピーしてください。")


if __name__ == "__main__":
    main()
