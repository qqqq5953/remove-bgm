"""Bandit v2 分離後端：權重下載、裝置選擇、分段推論。"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from pathlib import Path

import numpy as np

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch  # noqa: E402

from .bandit_net import Bandit, normalize_state_dict  # noqa: E402
from .base import ProgressFn  # noqa: E402

log = logging.getLogger(__name__)

ZENODO_RECORD = "12701995"
ZENODO_API = f"https://zenodo.org/api/records/{ZENODO_RECORD}"
MODEL_STEMS = ["speech", "music", "sfx"]
# 模型輸出的 stem 名稱 → 本程式使用的名稱
STEM_NAMES = {"speech": "dialogue", "music": "music", "sfx": "effects"}
SAMPLE_RATE = 48000
# 優先用多語言版本（中文、日文、韓文片表現較好），找不到再用其他版本
CHECKPOINT_PREFERENCE = [r"multi", r"eng", r""]


def cache_dir() -> Path:
    env = os.environ.get("REMOVE_BGM_MODEL_DIR")
    if env:
        return Path(env)
    if os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Caches" / "remove-bgm" / "models"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "remove-bgm" / "models"


def converted_path() -> Path:
    return cache_dir() / "bandit-v2.pt"


def is_model_ready() -> bool:
    return converted_path().exists()


def pick_checkpoint(files: list[dict]) -> dict:
    """從 Zenodo 檔案清單挑一個 checkpoint。"""
    ckpts = [f for f in files if f["key"].endswith((".ckpt", ".pt", ".pth"))]
    if not ckpts:
        raise RuntimeError("Zenodo 上找不到模型權重檔")
    for pattern in CHECKPOINT_PREFERENCE:
        matches = sorted(
            (f for f in ckpts if re.search(pattern, f["key"], re.IGNORECASE)), key=lambda f: f["key"]
        )
        if matches:
            return matches[0]
    raise AssertionError("unreachable")


def _download(url: str, dest: Path, progress: ProgressFn | None) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    tmp.replace(dest)


def ensure_model(progress: ProgressFn | None = None) -> Path:
    """確保權重已下載並轉成精簡格式，回傳檔案路徑。"""
    target = converted_path()
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)

    with urllib.request.urlopen(ZENODO_API, timeout=30) as resp:
        record = json.load(resp)
    chosen = pick_checkpoint(record["files"])
    raw = target.parent / chosen["key"]
    if not raw.exists():
        log.info("下載模型權重 %s", chosen["key"])
        _download(chosen["links"]["self"], raw, progress)

    convert_checkpoint(raw, target)
    raw.unlink()
    return target


def convert_checkpoint(raw: Path, target: Path) -> None:
    """把 Lightning checkpoint 轉成只有權重的檔案，之後可用 weights_only 安全載入。"""
    try:
        ckpt = torch.load(raw, map_location="cpu", weights_only=True)
    except Exception:
        # 官方 checkpoint 內含訓練設定物件，需要完整 unpickle（來源固定為 Zenodo 官方紀錄）
        ckpt = torch.load(raw, map_location="cpu", weights_only=False)
    state = ckpt.get("state_dict", ckpt)
    state = normalize_state_dict(state) if any(k.startswith("model.") for k in state) else state
    torch.save(state, target)


def pick_device() -> torch.device:
    if os.environ.get("REMOVE_BGM_DEVICE"):
        return torch.device(os.environ["REMOVE_BGM_DEVICE"])
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def build_model(weights: Path | None) -> Bandit:
    model = Bandit(MODEL_STEMS, fs=SAMPLE_RATE)
    if weights is not None:
        state = torch.load(weights, map_location="cpu", weights_only=True)
        missing, unexpected = model.load_state_dict(state, strict=False)
        missing = [k for k in missing if "freq_weights" not in k]
        if missing or unexpected:
            raise RuntimeError(f"模型權重格式不符：缺少 {missing[:5]}，多出 {unexpected[:5]}")
    return model.eval()


class BanditSeparator:
    sample_rate = SAMPLE_RATE

    def __init__(
        self,
        model: Bandit | None = None,
        device: torch.device | None = None,
        chunk_seconds: float = 8.0,
        hop_seconds: float = 4.0,
        batch_size: int = 4,
    ):
        self._model = model
        self.device = device or pick_device()
        self.chunk = int(chunk_seconds * SAMPLE_RATE)
        self.hop = int(hop_seconds * SAMPLE_RATE)
        self.batch_size = batch_size

    def is_ready(self) -> bool:
        return self._model is not None or is_model_ready()

    def prepare(self, progress: ProgressFn | None = None) -> None:
        """下載（第一次）並載入模型權重。"""
        if self._model is None:
            self._model = build_model(ensure_model(progress))

    def _get_model(self) -> Bandit:
        self.prepare()
        assert self._model is not None
        return self._model

    def separate(self, audio: np.ndarray, progress: ProgressFn | None = None) -> dict[str, np.ndarray]:
        model = self._get_model()
        try:
            out = self._run(model, audio, self.device, progress)
        except RuntimeError as e:
            if self.device.type == "cpu":
                raise
            # MPS／CUDA 遇到不支援的運算時退回 CPU 重跑
            log.warning("%s 上執行失敗，改用 CPU：%s", self.device, e)
            self.device = torch.device("cpu")
            out = self._run(model, audio, self.device, progress)
        return {STEM_NAMES[k]: v for k, v in out.items()}

    def _run(self, model: Bandit, audio: np.ndarray, device: torch.device, progress: ProgressFn | None):
        model.to(device)
        n_channels, n_samples = audio.shape
        chunk, hop = self.chunk, self.hop

        # 前後補零，讓每個取樣點都被完整的視窗涵蓋
        front = chunk - hop
        n_chunks = max(1, -(-(n_samples + front) // hop))
        total = (n_chunks - 1) * hop + chunk
        padded = np.zeros((n_channels, total), dtype=np.float32)
        padded[:, front : front + n_samples] = audio

        window = torch.hann_window(chunk, periodic=True).numpy().astype(np.float32)
        acc = {stem: np.zeros((n_channels, total), dtype=np.float32) for stem in model.stems}
        weight = np.zeros(total, dtype=np.float32)

        # 每個聲道當成獨立的單聲道輸入（模型 in_channels=1）
        starts = [i * hop for i in range(n_chunks)]
        for b in range(0, n_chunks, self.batch_size):
            batch_starts = starts[b : b + self.batch_size]
            x = np.stack([padded[:, s : s + chunk] for s in batch_starts])  # (B, C, T)
            x_t = torch.from_numpy(x.reshape(-1, 1, chunk)).to(device)
            with torch.inference_mode():
                y = model(x_t)
            for stem, val in y.items():
                val = val.float().cpu().numpy().reshape(len(batch_starts), n_channels, chunk)
                for i, s in enumerate(batch_starts):
                    acc[stem][:, s : s + chunk] += val[i] * window
            for s in batch_starts:
                weight[s : s + chunk] += window
            if progress:
                progress(min(1.0, (b + len(batch_starts)) / n_chunks))

        weight = np.maximum(weight, 1e-8)
        return {
            stem: (val / weight)[:, front : front + n_samples].astype(np.float32)
            for stem, val in acc.items()
        }
