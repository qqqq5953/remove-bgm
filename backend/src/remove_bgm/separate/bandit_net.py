"""Bandit（band-split RNN）電影音軌三軌分離模型。

移植自 https://github.com/kwatcharasupat/bandit-v2 （Apache License 2.0，
Copyright Karn N. Watcharasupat 等人），只保留推論需要的部分：
- 拿掉 pytorch_lightning、librosa、torchaudio 依賴，STFT 改用 torch.stft 實作
  （與 torchaudio Spectrogram(normalized=True) 數值相同）
- 拿掉訓練用的 gradient checkpointing 與 torch.compile 包裝
模組與屬性名稱維持跟原始碼一致，才能直接載入官方釋出的權重。
"""

from __future__ import annotations

import numpy as np
import torch
from torch import nn


# ---------------------------------------------------------------------------
# 頻帶切分（musical filterbank）
# ---------------------------------------------------------------------------


def _hz_to_midi(hz):
    return 12 * (np.log2(np.asanyarray(hz)) - np.log2(440.0)) + 69


def _midi_to_hz(midi):
    return 440.0 * (2.0 ** ((np.asanyarray(midi) - 69.0) / 12.0))


def musical_filterbank(n_bands: int, fs: int, n_freqs: int) -> torch.Tensor:
    nfft = 2 * (n_freqs - 1)
    df = fs / nfft
    f_max = fs / 2
    f_min = fs / nfft

    n_octaves = np.log2(f_max / f_min)
    bandwidth_mult = np.power(2.0, n_octaves / n_bands)

    low_midi = max(0, _hz_to_midi(f_min))
    high_midi = _hz_to_midi(f_max)
    hz_pts = _midi_to_hz(np.linspace(low_midi, high_midi, n_bands))

    low_bins = np.floor(hz_pts / bandwidth_mult / df).astype(int)
    high_bins = np.ceil(hz_pts * bandwidth_mult / df).astype(int)

    fb = np.zeros((n_bands, n_freqs))
    for i in range(n_bands):
        fb[i, low_bins[i] : high_bins[i] + 1] = 1.0
    fb[0, : low_bins[0]] = 1.0
    fb[-1, high_bins[-1] + 1 :] = 1.0

    return torch.as_tensor(fb)


class MusicalBandsplitSpecification:
    def __init__(self, nfft: int, fs: int, n_bands: int) -> None:
        filterbank = musical_filterbank(n_bands, fs, nfft // 2 + 1)
        normalized = filterbank / torch.sum(filterbank, dim=0, keepdim=True)

        self.band_specs: list[tuple[int, int]] = []
        self.freq_weights: list[torch.Tensor] = []
        for i in range(n_bands):
            active = torch.nonzero(filterbank[i, :]).squeeze(-1).tolist()
            if len(active) == 0:
                continue
            start, end = active[0], active[-1] + 1
            self.band_specs.append((start, end))
            self.freq_weights.append(normalized[i, start:end])

    def get_band_specs(self):
        return self.band_specs

    def get_freq_weights(self):
        return self.freq_weights


# ---------------------------------------------------------------------------
# Band split
# ---------------------------------------------------------------------------


class NormFC(nn.Module):
    def __init__(self, emb_dim: int, bandwidth: int, in_channels: int) -> None:
        super().__init__()
        fc_in = bandwidth * 2 * in_channels
        self.combined = nn.Sequential(nn.LayerNorm(fc_in), nn.Linear(fc_in, emb_dim))

    def forward(self, xb):
        return self.combined(xb)


class BandSplitModule(nn.Module):
    def __init__(self, band_specs, emb_dim: int, in_channels: int) -> None:
        super().__init__()
        self.band_specs = band_specs
        self.n_bands = len(band_specs)
        self.emb_dim = emb_dim
        self.norm_fc_modules = nn.ModuleList(
            [NormFC(emb_dim, end - start, in_channels) for start, end in band_specs]
        )

    def forward(self, x: torch.Tensor):
        # x：複數頻譜 (batch, in_chan, n_freq, n_time)
        batch, _, _, n_time = x.shape
        z = torch.zeros((batch, self.n_bands, n_time, self.emb_dim), device=x.device)
        x = torch.permute(x, (0, 3, 1, 2)).contiguous()
        for i, nfm in enumerate(self.norm_fc_modules):
            fstart, fend = self.band_specs[i]
            xb = torch.view_as_real(x[:, :, :, fstart:fend])
            z[:, i, :, :] = nfm(torch.reshape(xb, (batch, n_time, -1)))
        return z


# ---------------------------------------------------------------------------
# 時頻建模（交替在時間軸與頻帶軸上跑 RNN）
# ---------------------------------------------------------------------------


class ResidualRNN(nn.Module):
    def __init__(self, emb_dim: int, rnn_dim: int, bidirectional: bool, rnn_type: str):
        super().__init__()
        self.norm = nn.LayerNorm(emb_dim)
        self.rnn = getattr(nn, rnn_type)(
            input_size=emb_dim,
            hidden_size=rnn_dim,
            num_layers=1,
            batch_first=True,
            bidirectional=bidirectional,
        )
        self.fc = nn.Linear(rnn_dim * (2 if bidirectional else 1), emb_dim)

    def forward(self, z):
        z0 = z
        z = self.norm(z)
        batch, n_uncrossed, n_across, emb_dim = z.shape
        z = torch.reshape(z, (batch * n_uncrossed, n_across, emb_dim))
        z = self.rnn(z)[0]
        z = torch.reshape(z, (batch, n_uncrossed, n_across, -1))
        return self.fc(z) + z0


class Transpose(nn.Module):
    def __init__(self, dim0: int, dim1: int) -> None:
        super().__init__()
        self.dim0 = dim0
        self.dim1 = dim1

    def forward(self, z):
        return z.transpose(self.dim0, self.dim1)


class SeqBandModellingModule(nn.Module):
    def __init__(self, n_modules: int, emb_dim: int, rnn_dim: int, bidirectional: bool, rnn_type: str):
        super().__init__()
        layers: list[nn.Module] = []
        for _ in range(2 * n_modules):
            layers += [ResidualRNN(emb_dim, rnn_dim, bidirectional, rnn_type), Transpose(1, 2)]
        self.seqband = nn.Sequential(*layers)

    def forward(self, z):
        return self.seqband(z)


# ---------------------------------------------------------------------------
# 遮罩估計
# ---------------------------------------------------------------------------


class NormMLP(nn.Module):
    def __init__(self, emb_dim: int, mlp_dim: int, bandwidth: int, in_channels: int,
                 hidden_activation: str, complex_mask: bool):
        super().__init__()
        self.bandwidth = bandwidth
        self.in_channels = in_channels
        self.complex_mask = complex_mask
        self.reim = 2 if complex_mask else 1

        # 跟原始碼一樣，norm/hidden/output 同時掛在自己與 combined 底下（共用參數），
        # 這樣權重檔裡兩組 key 都對得上
        self.norm = nn.LayerNorm(emb_dim)
        self.hidden = nn.Sequential(nn.Linear(emb_dim, mlp_dim), getattr(nn, hidden_activation)())
        self.output = nn.Sequential(
            nn.Linear(mlp_dim, bandwidth * in_channels * self.reim * 2),
            nn.GLU(dim=-1),
        )
        self.combined = nn.Sequential(self.norm, self.hidden, self.output)

    def forward(self, qb):
        mb = self.combined(qb)
        batch, n_time, _ = mb.shape
        if self.complex_mask:
            mb = mb.reshape(batch, n_time, self.in_channels, self.bandwidth, self.reim).contiguous()
            mb = torch.view_as_complex(mb)
        else:
            mb = mb.reshape(batch, n_time, self.in_channels, self.bandwidth)
        return torch.permute(mb, (0, 2, 3, 1))  # (batch, in_channels, bandwidth, n_time)


class OverlappingMaskEstimationModule(nn.Module):
    def __init__(self, in_channels: int, band_specs, freq_weights, n_freq: int, emb_dim: int,
                 mlp_dim: int, hidden_activation: str, complex_mask: bool, use_freq_weights: bool):
        super().__init__()
        self.n_freq = n_freq
        self.band_specs = band_specs
        self.in_channels = in_channels
        self.norm_mlp = nn.ModuleList(
            [
                NormMLP(emb_dim, mlp_dim, end - start, in_channels, hidden_activation, complex_mask)
                for start, end in band_specs
            ]
        )
        self.use_freq_weights = use_freq_weights
        if use_freq_weights:
            for i, fw in enumerate(freq_weights):
                self.register_buffer(f"freq_weights/{i}", fw)

    def forward(self, q):
        batch, n_bands, n_time, _ = q.shape
        masks = torch.zeros(
            (batch, self.in_channels, self.n_freq, n_time), device=q.device, dtype=torch.complex64
        )
        for im in range(n_bands):
            fstart, fend = self.band_specs[im]
            mask = self.norm_mlp[im](q[:, im, :, :])
            if self.use_freq_weights:
                mask = mask * self.get_buffer(f"freq_weights/{im}")[:, None]
            masks[:, :, fstart:fend, :] += mask
        return masks


# ---------------------------------------------------------------------------
# STFT（等同 torchaudio Spectrogram / InverseSpectrogram，normalized=True）
# ---------------------------------------------------------------------------


class _STFT(nn.Module):
    def __init__(self, n_fft: int, hop_length: int, win_length: int, pad_mode: str):
        super().__init__()
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.win_length = win_length
        self.pad_mode = pad_mode
        self.register_buffer("window", torch.hann_window(win_length), persistent=False)

    def _norm(self):
        return self.window.pow(2.0).sum().sqrt()

    def forward(self, audio: torch.Tensor) -> torch.Tensor:
        # audio: (..., time) → (..., n_freq, n_frames) complex
        shape = audio.shape
        spec = torch.stft(
            audio.reshape(-1, shape[-1]),
            n_fft=self.n_fft,
            hop_length=self.hop_length,
            win_length=self.win_length,
            window=self.window,
            center=True,
            pad_mode=self.pad_mode,
            normalized=False,
            onesided=True,
            return_complex=True,
        )
        spec = spec / self._norm()
        return spec.reshape(shape[:-1] + spec.shape[-2:])

    def inverse(self, spec: torch.Tensor, length: int) -> torch.Tensor:
        shape = spec.shape
        spec = spec.reshape(-1, shape[-2], shape[-1]) * self._norm()
        audio = torch.istft(
            spec,
            n_fft=self.n_fft,
            hop_length=self.hop_length,
            win_length=self.win_length,
            window=self.window,
            center=True,
            normalized=False,
            onesided=True,
            length=length,
        )
        return audio.reshape(shape[:-2] + audio.shape[-1:])


# ---------------------------------------------------------------------------
# Bandit
# ---------------------------------------------------------------------------


class Bandit(nn.Module):
    """輸入 (batch, 1, samples) 的單聲道音訊，輸出每個 stem 的 (batch, 1, samples)。"""

    def __init__(
        self,
        stems: list[str],
        fs: int = 48000,
        in_channels: int = 1,
        n_bands: int = 64,
        n_sqm_modules: int = 8,
        emb_dim: int = 128,
        rnn_dim: int = 256,
        bidirectional: bool = True,
        rnn_type: str = "GRU",
        mlp_dim: int = 512,
        hidden_activation: str = "Tanh",
        complex_mask: bool = True,
        use_freq_weights: bool = True,
        n_fft: int = 2048,
        win_length: int = 2048,
        hop_length: int = 512,
        pad_mode: str = "reflect",
    ):
        super().__init__()
        self.stems = list(stems)
        self.fs = fs
        self.in_channels = in_channels

        self.spectral = _STFT(n_fft, hop_length, win_length, pad_mode)
        self.band_specs = MusicalBandsplitSpecification(nfft=n_fft, fs=fs, n_bands=n_bands)
        self.band_split = BandSplitModule(self.band_specs.get_band_specs(), emb_dim, in_channels)
        self.tf_model = SeqBandModellingModule(n_sqm_modules, emb_dim, rnn_dim, bidirectional, rnn_type)
        self.mask_estim = nn.ModuleDict(
            {
                stem: OverlappingMaskEstimationModule(
                    in_channels=in_channels,
                    band_specs=self.band_specs.get_band_specs(),
                    freq_weights=self.band_specs.get_freq_weights(),
                    n_freq=n_fft // 2 + 1,
                    emb_dim=emb_dim,
                    mlp_dim=mlp_dim,
                    hidden_activation=hidden_activation,
                    complex_mask=complex_mask,
                    use_freq_weights=use_freq_weights,
                )
                for stem in self.stems
            }
        )

    def forward(self, audio: torch.Tensor) -> dict[str, torch.Tensor]:
        length = audio.shape[-1]
        x = self.spectral(audio)
        q = self.tf_model(self.band_split(x))
        out = {}
        for stem, mem in self.mask_estim.items():
            s = x * mem(q).to(x.dtype)
            out[stem] = self.spectral.inverse(s, length)
        return out


def normalize_state_dict(state_dict: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    """把 Lightning checkpoint 的 key 轉成本模組的 key。"""
    out = {}
    for key, value in state_dict.items():
        if not key.startswith("model."):
            continue  # loss、metric 等訓練用的東西
        key = key[len("model."):].replace("_orig_mod.", "")
        if key.startswith(("stft.", "istft.")):
            continue  # torchaudio 的 window buffer，本模組自己算
        out[key] = value
    return out

