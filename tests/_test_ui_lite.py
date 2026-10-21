# -*- coding: utf-8 -*-
"""Tkinter 界面逻辑测试：进度、连点后门绑定、首次设置提交、控制面板刷新。

需要图形环境（Windows 桌面，或 Linux 下的 xvfb）。
不启动客户端，使用临时数据目录。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ['LOCALAPPDATA'] = tempfile.mkdtemp(prefix='pctl_ui_')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402

from core import store  # noqa: E402

# 测试使用临时数据库；同时屏蔽真实注册表，避免影响开发机当天用量
store._reg_write_usage = lambda d, sec: None
store._reg_read_usage = lambda d: 0

from ui.lock_screen import (BsodProgress, BsodScreen, FakePopup,  # noqa: E402
                            CLICK_TARGET, prompt_unlock)

ok = True


def check(name, cond, extra=''):
    global ok
    print(('  PASS  ' if cond else '  FAIL  ') + name + (('  -> ' + str(extra)) if extra and not cond else ''))
    if not cond:
        ok = False


root = tk.Tk()
root.withdraw()


# ------------------------------------------------------------------ 进度

print('=== 蓝屏进度 ===')
p = BsodProgress()
values = [p.step() for _ in range(120)]
check('进度单调不减', all(b >= a for a, b in zip(values, values[1:])))
check('进度上限 100', max(values) == 100, max(values))
check('进度能到 100', p.value == 100, p.value)


# ------------------------------------------------------------------ 连点绑定

print('=== 蓝屏连点绑定 ===')
calls = []
w = BsodScreen(root, (0, 0, 400, 300), primary=True, on_backdoor=lambda win: calls.append(win))
w.withdraw()
for _ in range(CLICK_TARGET - 1):
    w._click()
check('9 次不触发', len(calls) == 0, len(calls))
w._click()
check('第 10 次触发', len(calls) == 1, len(calls))
w.destroy()

calls.clear()
f = FakePopup(root, (0, 0, 400, 300), on_backdoor=lambda win: calls.append(win))
f.withdraw()
for _ in range(CLICK_TARGET):
    f._click()
check('假弹窗同样支持连点', len(calls) == 1, len(calls))
f.destroy()


# ------------------------------------------------------------------ 首次设置

print('=== 首次设置窗口 ===')
done = []
exited = []
from ui.panel import SetupWindow, ControlPanel  # noqa: E402

check('初始未初始化', store.is_initialized() is False)
s = SetupWindow(root, lambda: done.append(1), lambda: exited.append(1))
s.withdraw()
s.admin.insert(0, '123')          # 太短
s.unlock.insert(0, '8888')
s._submit()
check('管理密码太短被拒绝', not done and '至少 6 位' in s.err.cget('text'), s.err.cget('text'))
s.admin.delete(0, 'end')
s.admin.insert(0, 'admin123')
s.quota.delete(0, 'end')
s.quota.insert(0, '60')
s._submit()
check('设置完成回调', done == [1], done)
check('密码已写入', store.is_initialized() is True)
check('额度已写入', int(store.get_settings()['quota_minutes']) == 60)
check('默认待机', int(store.get_settings()['enabled']) == 0 and
      int(store.get_settings()['locked']) == 0)

print('=== 后门密码框 ===')
store.update_settings(locked=1)
store.add_used(30)
bumps_before = int(store.get_usage_row().get('unlock_count') or 0)


def enter_password():
    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel) and hasattr(w, 'entry'):
            w.entry.insert(0, '8888')
            w._on_return()
            return


root.after(400, enter_password)
unlocked = prompt_unlock(root, guard=None)
check('正确密码解锁成功', unlocked is True)
check('锁定状态已清除', int(store.get_settings()['locked']) == 0)
check('今日用量已清零', store.get_used() == 0)
check('解锁次数 +1', int(store.get_usage_row().get('unlock_count') or 0) == bumps_before + 1)


# ------------------------------------------------------------------ 控制面板

print('=== 控制面板 ===')


class FakeApp:
    def __init__(self):
        self.calls = []
        self.state = {'locked': False, 'enabled': False, 'remaining': 7200,
                      'hotkey': 'Ctrl+Alt+M', 'port': 8080,
                      'url': 'http://127.0.0.1:8080', 'tray_hidden': 0}

    def snapshot(self):
        return dict(self.state)

    def open_web(self):
        self.calls.append('web')

    def panel_start(self):
        self.calls.append('start')
        self.state['enabled'] = True

    def panel_pause(self):
        self.calls.append('pause')
        self.state['enabled'] = False

    def panel_lock(self):
        self.calls.append('lock')
        self.state['locked'] = True

    def panel_unlock(self):
        self.calls.append('unlock')
        self.state['locked'] = False

    def save_hotkey(self, text):
        return True, text

    def apply_port(self, text):
        return True, 'ok'

    def set_tray_hidden(self, hidden):
        return True, ''

    def request_exit(self, parent):
        self.calls.append('exit')
        return False


fake = FakeApp()
panel = ControlPanel(root, fake)
panel.withdraw()
panel.refresh()
check('面板显示剩余时间', '今日剩余' in panel.time_lbl.cget('text'), panel.time_lbl.cget('text'))
check('待机时按钮为开始', panel.btn_main.cget('text') == '开始管控', panel.btn_main.cget('text'))
panel._toggle_main()
check('点击开始调用 panel_start', fake.calls == ['start'], fake.calls)
panel.refresh()
check('开始后按钮为暂停', panel.btn_main.cget('text') == '暂停管控', panel.btn_main.cget('text'))
panel._toggle_lock()
check('点击锁定调用 panel_lock', fake.calls[-1] == 'lock', fake.calls)
panel.destroy()
root.destroy()

print()
print('=== 全部通过 ===' if ok else '=== 存在失败项 ===')
sys.exit(0 if ok else 1)
