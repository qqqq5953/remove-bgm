from __future__ import annotations

from typing import Callable, Protocol

import numpy as np

STEMS = ("dialogue", "music", "effects")

ProgressFn = Callable[[float], None]


class Separator(Protocol):
    """把混音拆成對白、音樂、音效三軌。"""

    sample_rate: int

    def separate(self, audio: np.ndarray, progress: ProgressFn | None = None) -> dict[str, np.ndarray]:
        """audio: (channels, samples) float32，取樣率為 self.sample_rate。

        回傳 {"dialogue", "music", "effects"} → 跟輸入同形狀的陣列。
        """
        ...
