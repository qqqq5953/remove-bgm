import numpy as np
import pytest
import torch
from torch import nn

from remove_bgm.separate import bandit
from remove_bgm.separate.bandit import BanditSeparator
from remove_bgm.separate.bandit_net import Bandit, normalize_state_dict


class RatioModel(nn.Module):
    """假模型：依固定比例輸出三軌，用來驗證分段接合是否無縫。"""

    stems = ["speech", "music", "sfx"]

    def __init__(self, fail_on: str | None = None):
        super().__init__()
        self.fail_on = fail_on
        self.devices: list[str] = []

    def forward(self, x):
        self.devices.append(x.device.type)
        if self.fail_on and x.device.type == self.fail_on:
            raise RuntimeError("operator not implemented")
        return {"speech": x * 0.5, "music": x * 0.3, "sfx": x * 0.2}


@pytest.mark.parametrize("n_seconds", [0.3, 1.0, 2.0, 3.7, 9.1])
def test_chunked_overlap_add_reconstructs_signal(n_seconds):
    rng = np.random.default_rng(0)
    audio = rng.standard_normal((2, int(n_seconds * 48000))).astype(np.float32) * 0.1
    sep = BanditSeparator(model=RatioModel(), device=torch.device("cpu"), chunk_seconds=1.0, hop_seconds=0.5, batch_size=3)
    progress = []
    out = sep.separate(audio, progress.append)

    assert set(out) == {"dialogue", "music", "effects"}
    for name, ratio in (("dialogue", 0.5), ("music", 0.3), ("effects", 0.2)):
        assert out[name].shape == audio.shape
        np.testing.assert_allclose(out[name], audio * ratio, atol=1e-5)
    assert progress[-1] == 1.0 and progress == sorted(progress)


def test_falls_back_to_cpu_when_device_fails(monkeypatch):
    model = RatioModel(fail_on="meta")
    sep = BanditSeparator(model=model, device=torch.device("cpu"), chunk_seconds=1.0, hop_seconds=0.5)
    # 模擬 MPS：假裝目前裝置不是 CPU，第一次執行失敗
    sep.device = torch.device("meta")
    calls = {"n": 0}
    real_run = sep._run

    def run(model_, audio, device, progress):
        calls["n"] += 1
        if device.type != "cpu":
            raise RuntimeError("MPS 不支援的運算")
        return real_run(model_, audio, device, progress)

    monkeypatch.setattr(sep, "_run", run)
    audio = np.ones((2, 48000), dtype=np.float32) * 0.1
    out = sep.separate(audio)
    assert calls["n"] == 2 and sep.device.type == "cpu"
    np.testing.assert_allclose(out["dialogue"], audio * 0.5, atol=1e-5)


def test_cpu_failure_is_not_retried():
    sep = BanditSeparator(model=RatioModel(fail_on="cpu"), device=torch.device("cpu"))
    with pytest.raises(RuntimeError):
        sep.separate(np.zeros((2, 4800), dtype=np.float32))


def test_real_architecture_runs_end_to_end():
    """用隨機權重跑完整的 Bandit 架構（輸出無意義，但確認形狀與流程正確）。"""
    torch.manual_seed(0)
    model = Bandit(["speech", "music", "sfx"]).eval()
    sep = BanditSeparator(model=model, device=torch.device("cpu"), chunk_seconds=1.0, hop_seconds=0.5, batch_size=2)
    audio = (np.random.default_rng(1).standard_normal((2, 72000)) * 0.1).astype(np.float32)
    out = sep.separate(audio)
    for v in out.values():
        assert v.shape == audio.shape and np.isfinite(v).all()


def test_stft_round_trip():
    model = Bandit(["speech"])
    x = torch.randn(3, 1, 48000)
    y = model.spectral.inverse(model.spectral(x), x.shape[-1])
    torch.testing.assert_close(y, x, atol=1e-4, rtol=1e-4)


def test_band_specs_cover_all_bins():
    model = Bandit(["speech"])
    specs = model.band_specs.get_band_specs()
    assert specs[0][0] == 0 and specs[-1][1] == 1025
    assert len(specs) == 64


def test_normalize_state_dict():
    state = {
        "model.band_split.x": 1,
        "model.tf_model._orig_mod.seqband.0.rnn.weight": 2,
        "model.stft.window": 3,
        "loss_handler.foo": 4,
    }
    assert normalize_state_dict(state) == {"band_split.x": 1, "tf_model.seqband.0.rnn.weight": 2}


def test_build_model_loads_converted_weights(tmp_path):
    src = Bandit(bandit.MODEL_STEMS)
    lightning_like = {"state_dict": {f"model.{k}": v for k, v in src.state_dict().items()}, "hyper": "x"}
    raw = tmp_path / "raw.ckpt"
    torch.save(lightning_like, raw)
    converted = tmp_path / "bandit-v2.pt"
    bandit.convert_checkpoint(raw, converted)
    loaded = bandit.build_model(converted)
    for (k1, v1), (k2, v2) in zip(src.state_dict().items(), loaded.state_dict().items(), strict=True):
        assert k1 == k2 and torch.equal(v1, v2)


def test_build_model_rejects_wrong_weights(tmp_path):
    path = tmp_path / "bad.pt"
    torch.save({"something.else": torch.zeros(1)}, path)
    with pytest.raises(RuntimeError, match="模型權重格式不符"):
        bandit.build_model(path)


@pytest.mark.parametrize(
    "keys,expected",
    [
        (["readme.md", "bandit-v2-eng.ckpt", "bandit-v2-multi.ckpt"], "bandit-v2-multi.ckpt"),
        (["eng.ckpt", "deu.ckpt"], "eng.ckpt"),
        (["deu.ckpt", "cmn.ckpt"], "cmn.ckpt"),
    ],
)
def test_pick_checkpoint(keys, expected):
    files = [{"key": k, "links": {"self": f"https://z/{k}"}} for k in keys]
    assert bandit.pick_checkpoint(files)["key"] == expected


def test_pick_checkpoint_none():
    with pytest.raises(RuntimeError, match="找不到模型權重檔"):
        bandit.pick_checkpoint([{"key": "readme.md"}])


def test_cache_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("REMOVE_BGM_MODEL_DIR", str(tmp_path))
    assert bandit.cache_dir() == tmp_path
    assert not bandit.is_model_ready()
    (tmp_path / "bandit-v2.pt").write_bytes(b"")
    assert bandit.is_model_ready()


def test_pick_device_env(monkeypatch):
    monkeypatch.setenv("REMOVE_BGM_DEVICE", "cpu")
    assert bandit.pick_device().type == "cpu"
