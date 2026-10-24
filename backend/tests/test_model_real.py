"""用官方權重的真實模型測試。需要能連到 zenodo.org，連不到時自動略過（不算通過）。

執行：uv run pytest -m model
"""

import urllib.error

import numpy as np
import pytest
import torch

from remove_bgm import media
from remove_bgm.separate import bandit

pytestmark = pytest.mark.model


@pytest.fixture(scope="module")
def real_separator():
    try:
        bandit.ensure_model()
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(f"無法下載模型權重（{e}）")
    return bandit.BanditSeparator(device=torch.device("cpu"))


def snr_db(ref: np.ndarray, est: np.ndarray) -> float:
    return float(10 * np.log10(np.sum(ref**2) / max(np.sum((ref - est) ** 2), 1e-12)))


def test_real_model_separates(real_separator, youtube_like_video, tmp_path):
    mixture = media.extract_audio(youtube_like_video, tmp_path / "m.wav", 0, 4.0)
    out = real_separator.separate(mixture)
    assert set(out) == {"dialogue", "music", "effects"}
    for v in out.values():
        assert v.shape == mixture.shape and np.isfinite(v).all()
    # 訓練好的模型，三軌加起來應該很接近原音
    total = out["dialogue"] + out["music"] + out["effects"]
    assert snr_db(mixture, total) > 10
