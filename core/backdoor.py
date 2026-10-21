# -*- coding: utf-8 -*-
"""锁屏连点后门计数（纯逻辑，便于单元测试）。

规则：在 window 秒内累计 target 次点击即触发；触发后计数清零；
超过时间窗口的旧点击自动丢弃。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import time


class ClickCounter:
    def __init__(self, target: int = 10, window: float = 6.0, clock=time.monotonic):
        self.target = max(1, int(target))
        self.window = max(0.1, float(window))
        self._clock = clock
        self._clicks: list[float] = []

    def click(self, now: float | None = None) -> bool:
        """记录一次点击；达到阈值返回 True 并清零。"""
        t = float(self._clock() if now is None else now)
        self._clicks = [x for x in self._clicks if t - x <= self.window]
        self._clicks.append(t)
        if len(self._clicks) >= self.target:
            self._clicks.clear()
            return True
        return False

    def reset(self) -> None:
        self._clicks.clear()

    @property
    def count(self) -> int:
        return len(self._clicks)
