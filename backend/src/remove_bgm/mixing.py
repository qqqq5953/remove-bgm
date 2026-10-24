from __future__ import annotations

import numpy as np

MIN_DB = -12.0
MAX_DB = 6.0


def db_to_gain(db: float) -> float:
    return float(10 ** (db / 20))


def mix(dialogue: np.ndarray, effects: np.ndarray, dialogue_db: float = 0.0, effects_db: float = 0.0) -> np.ndarray:
    """依增益混合對白與音效；混完超過 0dBFS 時整體降低音量，避免爆音。"""
    out = dialogue * db_to_gain(dialogue_db) + effects * db_to_gain(effects_db)
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 1.0:
        out = out * (0.999 / peak)
    return out.astype(np.float32)
