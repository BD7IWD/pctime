# -*- coding: utf-8 -*-
"""验证「连点 10 次」后门计数：9 次不触发，第 10 次触发；超时不累计。

纯逻辑测试，不依赖 Qt / Tkinter 窗口，可在无桌面环境运行。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
import os
import sys
import tempfile
from pathlib import Path

# 导入 ui.lock_screen 会间接导入 core.config，把数据目录指到临时位置
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp(prefix='pctl_backdoor_')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.backdoor import ClickCounter  # noqa: E402
from ui.lock_screen import CLICK_TARGET, CLICK_WINDOW  # noqa: E402

ok = True


def check(name, cond, extra=''):
    global ok
    print(('  PASS  ' if cond else '  FAIL  ') + name + (('  -> ' + str(extra)) if extra and not cond else ''))
    if not cond:
        ok = False


print('=== 常量 ===')
check('CLICK_TARGET == 10', CLICK_TARGET == 10, CLICK_TARGET)
check('CLICK_WINDOW > 0', CLICK_WINDOW > 0, CLICK_WINDOW)

print('=== 9 次不触发，第 10 次触发 ===')
c = ClickCounter(CLICK_TARGET, CLICK_WINDOW)
results = [c.click(now=i * 0.3) for i in range(9)]
check('连点 9 次不触发', not any(results), results)
check('第 10 次触发', c.click(now=9 * 0.3) is True)
check('触发后计数清零', c.count == 0, c.count)

print('=== 触发后可重新计数 ===')
results = [c.click(now=10 + i * 0.3) for i in range(9)]
check('再次 9 次不触发', not any(results))
check('再次第 10 次触发', c.click(now=10 + 9 * 0.3) is True)

print('=== 时间窗口外不累计 ===')
c2 = ClickCounter(CLICK_TARGET, CLICK_WINDOW)
c2.click(now=0.0)                      # 很久以前的一次
results = [c2.click(now=CLICK_WINDOW + 2 + i * 0.2) for i in range(9)]
check('旧点击被清理，9 次仍不触发', not any(results))
check('第 10 次触发', c2.click(now=CLICK_WINDOW + 2 + 9 * 0.2) is True)

print('=== 自定义阈值 ===')
c3 = ClickCounter(3, 1.0)
check('3 次阈值前两次不触发', c3.click(now=0) is False and c3.click(now=0.1) is False)
check('第 3 次触发', c3.click(now=0.2) is True)

print()
print('=== 全部通过 ===' if ok else '=== 存在失败项 ===')
sys.exit(0 if ok else 1)
