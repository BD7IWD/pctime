# -*- coding: utf-8 -*-
"""Tkinter 锁定画面：

  1) 假 Windows 蓝屏（默认，最像"电脑坏了"）
  2) 假"电脑已损坏"错误弹窗

锁屏期间：静音、隐藏鼠标指针、键盘钩子吞掉所有按键。
家长用手机点「解除锁定」，或在画面上 6 秒内连点 10 次输入解锁密码。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import random
import tkinter as tk

from core import config, store
from core.backdoor import ClickCounter
from ui import win32

BSOD_BG = '#0078D7'      # Win10 蓝屏底色
BSOD_BG_11 = '#101418'   # Win11 黑屏底色

CLICK_TARGET = 10
CLICK_WINDOW = 6.0

FONT_CN = 'Microsoft YaHei UI'
FONT_EN = 'Segoe UI'


def _font(size: int, bold: bool = False, family: str = FONT_CN, pixels: bool = False):
    # Tk 字号为负数时表示像素；Canvas 自绘按屏幕宽度缩放，必须用像素
    n = max(8, int(size))
    return (family, -n if pixels else n, 'bold' if bold else 'normal')


class BsodProgress:
    """蓝屏进度：随机步进，走到 100% 后保持。"""

    def __init__(self, rng: random.Random | None = None):
        self._rng = rng or random.Random()
        self.value = 0

    def step(self) -> int:
        if self.value < 100:
            self.value = min(100, self.value + self._rng.randint(2, 4))
        return self.value


class _LockWindow(tk.Toplevel):
    """全屏、无边框、置顶、隐藏光标的锁屏窗口基类。"""

    def __init__(self, master, rect: tuple[int, int, int, int],
                 bg: str, on_backdoor=None):
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        self.configure(bg=bg)
        try:
            self.configure(cursor='none')      # 个别 Tk 构建可能不认 none，不影响锁屏
        except tk.TclError:
            pass
        self.protocol('WM_DELETE_WINDOW', lambda: None)   # Alt+F4 也不关闭
        self.rect = rect
        self._on_backdoor = on_backdoor
        self._counter = ClickCounter(CLICK_TARGET, CLICK_WINDOW)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        try:
            self.canvas.configure(cursor='none')
        except tk.TclError:
            pass
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Button-1>', self._click)
        self.canvas.bind('<Configure>', lambda e: self._draw())
        w, h = rect[2], rect[3]
        self.geometry(f'{w}x{h}+0+0')
        self.deiconify()
        self.lift()
        win32.place_topmost(self, rect)

    def _click(self, _ev=None):
        if self._counter.click() and self._on_backdoor is not None:
            self._on_backdoor(self)

    def _draw(self):
        pass


class BsodScreen(_LockWindow):
    """假蓝屏。主屏绘制全部内容，副屏只显示纯色。"""

    def __init__(self, master, rect, primary: bool = True,
                 code: str = 'CRITICAL_PROCESS_DIED', dark: bool = False,
                 on_backdoor=None):
        bg = BSOD_BG_11 if dark else BSOD_BG
        super().__init__(master, rect, bg, on_backdoor)
        self.primary = primary
        self.dark = dark
        self.code = code or 'CRITICAL_PROCESS_DIED'
        self._progress = BsodProgress()
        self._pct_item = None
        self._after_id = None
        if primary:
            self._draw()
            self._after_id = self.after(900, self._tick)
        else:
            self.canvas.configure(bg=bg)

    def _tick(self):
        try:
            if not self.winfo_exists():
                return
            self._progress.step()
            if self._pct_item is not None:
                self.canvas.itemconfigure(
                    self._pct_item, text=f'{self._progress.value}% 完成')
            if self._progress.value < 100:
                self._after_id = self.after(900, self._tick)
            else:
                self._after_id = None
        except tk.TclError:
            pass

    def _draw(self):
        # 超级构造函数映射窗口时会触发 <Configure>，此时子类字段还没建好
        if not hasattr(self, 'primary') or not hasattr(self, '_progress'):
            return
        c = self.canvas
        try:
            c.delete('all')
        except tk.TclError:
            return
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        bg = BSOD_BG_11 if self.dark else BSOD_BG
        c.configure(bg=bg)

        if not self.primary:
            c.create_rectangle(0, 0, w, h, fill=bg, outline='')
            return

        x = int(w * 0.09)
        y = int(h * 0.18)

        c.create_text(x, y, text=':)', anchor='nw', fill='#ffffff',
                      font=_font(w * 0.075, family=FONT_EN, pixels=True))
        y += int(w * 0.115)
        c.create_text(
            x, y,
            text='你的设备遇到问题，需要重新启动。我们只收集某些错误信息，然后为你重新启动。',
            anchor='nw', fill='#ffffff', font=_font(w * 0.0185, pixels=True))
        y += int(w * 0.033)
        self._pct_item = c.create_text(
            x, y, text=f'{self._progress.value}% 完成', anchor='nw',
            fill='#ffffff', font=_font(w * 0.0185, pixels=True))

        qr = max(64, int(w * 0.055))
        qy = int(h * 0.60)
        self._draw_qr(c, x, qy, qr)

        tx = x + qr + int(w * 0.02)
        base = qy + int(qr * 0.30)
        c.create_text(tx, base, text='有关此问题的详细信息和可能的解决方法，请访问',
                      anchor='nw', fill='#ffffff', font=_font(w * 0.0128, pixels=True))
        c.create_text(tx, base + int(w * 0.019), text='https://www.windows.com/stopcode',
                      anchor='nw', fill='#ffffff', font=_font(w * 0.0128, pixels=True))
        c.create_text(tx, base + int(w * 0.052), text='如果致电支持人员，请向他们提供以下信息：',
                      anchor='nw', fill='#ffffff', font=_font(w * 0.0128, pixels=True))
        c.create_text(tx, base + int(w * 0.074), text=f'停止代码: {self.code}',
                      anchor='nw', fill='#ffffff', font=_font(w * 0.0128, pixels=True))

    @staticmethod
    def _draw_qr(c: tk.Canvas, x: int, y: int, size: int) -> None:
        n = 25
        rnd = random.Random(0xB50D)
        grid = [[rnd.random() < 0.45 for _ in range(n)] for _ in range(n)]
        cell = size / n
        c.create_rectangle(x, y, x + size, y + size, fill='#ffffff', outline='')
        for gy in range(n):
            for gx in range(n):
                if grid[gy][gx]:
                    c.create_rectangle(
                        x + gx * cell, y + gy * cell,
                        x + (gx + 1) * cell + 1, y + (gy + 1) * cell + 1,
                        fill='#000000', outline='')
        for (ox, oy) in ((0, 0), (n - 7, 0), (0, n - 7)):
            c.create_rectangle(x + ox * cell, y + oy * cell,
                               x + (ox + 7) * cell, y + (oy + 7) * cell,
                               fill='#000000', outline='')
            c.create_rectangle(x + (ox + 1) * cell, y + (oy + 1) * cell,
                               x + (ox + 6) * cell, y + (oy + 6) * cell,
                               fill='#ffffff', outline='')
            c.create_rectangle(x + (ox + 2) * cell, y + (oy + 2) * cell,
                               x + (ox + 5) * cell, y + (oy + 5) * cell,
                               fill='#000000', outline='')


class FakePopup(_LockWindow):
    """假的 Windows 错误弹窗：「您的电脑已损坏」。"""

    def __init__(self, master, rect, on_backdoor=None):
        super().__init__(master, rect, '#e9edf4', on_backdoor)
        self._draw()

    def _draw(self):
        c = self.canvas
        try:
            c.delete('all')
        except tk.TclError:
            return
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        c.configure(bg='#e9edf4')
        c.create_rectangle(0, 0, w, h, fill='#e9edf4', outline='')

        dw, dh = 430, 300
        dx, dy = (w - dw) // 2, (h - dh) // 2
        c.create_rectangle(dx, dy, dx + dw, dy + dh, fill='#f0f0f0',
                           outline='#7c8a9e')
        c.create_rectangle(dx, dy, dx + dw, dy + 30, fill='#dfe4ec', outline='')
        c.create_text(dx + 8, dy + 7, text='Microsoft Windows', anchor='nw',
                      fill='#20304a', font=_font(12, pixels=True))
        c.create_text(dx + 22, dy + 48, text='❌', anchor='nw',
                      fill='#d1373a', font=_font(30, family=FONT_EN, pixels=True))
        c.create_text(
            dx + 78, dy + 50, anchor='nw', fill='#16202e', width=dw - 105,
            font=_font(13, pixels=True),
            text=('您的电脑已损坏。\n\n系统文件缺失或被篡改，无法继续使用。\n'
                  '请联系计算机维修人员处理。\n\n错误代码: 0x80070057'))
        bx, by = dx + dw - 112, dy + dh - 46
        c.create_rectangle(bx, by, bx + 92, by + 30, fill='#e6eaf1',
                           outline='#a8b3c4')
        c.create_text(bx + 46, by + 15, text='确定', fill='#16202e',
                      font=_font(13, pixels=True))


class _PwdDialog(tk.Toplevel):
    """解锁密码框：深色、置顶、手动居中。"""

    W, H = 470, 232

    def __init__(self, master):
        super().__init__(master)
        self.result = False
        self.on_submit = None
        self.withdraw()
        self.title('系统恢复')
        self.resizable(False, False)
        self.attributes('-topmost', True)
        self.configure(bg='#1b2430')
        self.protocol('WM_DELETE_WINDOW', self.cancel)

        px, py, pw, ph = win32.primary_rect()
        x = px + (pw - self.W) // 2
        y = py + (ph - self.H) // 2
        self.geometry(f'{self.W}x{self.H}+{x}+{y}')

        tk.Label(self, text='系统恢复', bg='#1b2430', fg='#e8eef7',
                 font=_font(17, True), anchor='w').pack(
            fill='x', padx=26, pady=(20, 4))
        tk.Label(self, text='输入恢复密钥以继续：', bg='#1b2430', fg='#93a4bb',
                 font=_font(12), anchor='w').pack(fill='x', padx=26)

        self.entry = tk.Entry(self, show='*', bg='#0f1620', fg='#ffffff',
                              insertbackground='#ffffff', relief='flat',
                              font=_font(16))
        self.entry.pack(fill='x', padx=26, pady=(10, 4), ipady=10)
        self.entry.bind('<Return>', self._on_return)
        self.bind('<Escape>', lambda e: self.cancel())

        self.err = tk.Label(self, text='', bg='#1b2430', fg='#ff9b9b',
                            font=_font(11), anchor='w')
        self.err.pack(fill='x', padx=26, pady=(2, 0))

        row = tk.Frame(self, bg='#1b2430')
        row.pack(fill='x', padx=26, pady=(10, 18))
        tk.Button(row, text='取消', command=self.cancel, relief='flat',
                  bg='#2a3547', fg='#cfdae8', activebackground='#35435a',
                  font=_font(12), width=8).pack(side='right', padx=(10, 0))
        tk.Button(row, text='确定', command=self._submit, relief='flat',
                  bg='#2d6cdf', fg='#ffffff', activebackground='#3d7cef',
                  font=_font(12), width=8).pack(side='right')

        self.deiconify()
        self.lift()
        self.grab_set()
        self.entry.focus_force()

    def password(self) -> str:
        return self.entry.get()

    def show_error(self, text: str) -> None:
        self.err.configure(text=text)

    def _on_return(self, _ev=None):
        # 返回 "break" 阻止事件继续传到 Toplevel 的同名绑定，避免校验两次
        self._submit()
        return 'break'

    def _submit(self) -> None:
        if self.result:
            return
        if self.on_submit is not None and self.on_submit():
            self.result = True
            self.destroy()
        return

    def cancel(self) -> None:
        self.result = False
        self.destroy()


def prompt_unlock(master, guard=None) -> bool:
    """弹出密码框并校验。返回 True 表示已解锁（并已写入数据库）。"""
    dlg = _PwdDialog(master)

    def submit() -> bool:
        pwd = dlg.password()
        if not pwd:
            dlg.show_error('请输入恢复密钥')
            return False
        s = store.get_settings()
        if s.get('unlock_hash') and store.verify_pwd(pwd, s['unlock_hash']):
            store.update_settings(locked=0, lock_reason=None)
            store.reset_used()
            store.bump_unlock()
            store.add_event('unlocked', '锁定画面后门：输入密码解锁')
            config.log('[backdoor] 密码正确，解锁')
            return True
        store.add_event('unlock_failed', '锁定画面后门：密码错误')
        config.log('[backdoor] 密码错误')
        dlg.show_error('恢复密钥不正确，请重试')
        dlg.entry.select_range(0, 'end')
        dlg.entry.focus_set()
        return False

    dlg.on_submit = submit
    if guard is not None:
        guard.set_typing(True)
    try:
        dlg.wait_window()
        return bool(dlg.result)
    finally:
        if guard is not None:
            guard.set_typing(False)


class LockManager:
    """管理所有显示器的锁定画面。"""

    def __init__(self, root: tk.Tk, guard=None, audio=None, on_local_unlocked=None):
        self.root = root
        self.guard = guard
        self.audio = audio
        self.on_local_unlocked = on_local_unlocked
        self.windows: list[tk.Toplevel] = []
        self._locked = False
        self._dialog_open = False
        self._keep_id = None

    @property
    def is_locked(self) -> bool:
        return self._locked

    def lock(self, style: str = 'bsod', code: str = 'CRITICAL_PROCESS_DIED') -> None:
        if self._locked:
            return
        self._locked = True

        rects = win32.list_monitors()
        px, py, pw, ph = win32.primary_rect()
        primary_idx = 0
        for i, r in enumerate(rects):
            if r == (px, py, pw, ph):
                primary_idx = i
                break

        for i, rect in enumerate(rects):
            try:
                if style == 'popup':
                    w = FakePopup(self.root, rect, on_backdoor=self._open_backdoor)
                else:
                    w = BsodScreen(self.root, rect, primary=(i == primary_idx),
                                   code=code, on_backdoor=self._open_backdoor)
                self.windows.append(w)
            except Exception as e:
                config.log(f'[lock] 创建锁定窗口失败: {e}')

        if not self.windows:
            try:
                w = BsodScreen(self.root, (px, py, pw, ph), primary=True,
                               code=code, on_backdoor=self._open_backdoor)
                self.windows.append(w)
            except Exception as e:
                config.log(f'[lock] 创建锁定窗口失败: {e}')

        if not self.windows:
            # 没有画面就不要静音/冻键盘，否则用户会面对一台“无声死机”的电脑
            self._locked = False
            config.log('[lock] 无法创建任何锁定窗口，放弃本次锁定')
            return

        if self.audio is not None:
            try:
                self.audio.mute()
            except Exception as e:
                config.log(f'[lock] 静音失败: {e}')
        if self.guard is not None:
            self.guard.set_active(True)
        win32.keep_awake(True)

        self._keep()
        config.log(f'[lock] 已锁定（样式={style}）')

    def _keep(self) -> None:
        if not self._locked:
            return
        if not self._dialog_open:
            for w in list(self.windows):
                try:
                    if w.winfo_exists():
                        w.lift()
                        w.attributes('-topmost', True)
                except Exception:
                    pass
        try:
            self._keep_id = self.root.after(900, self._keep)
        except Exception:
            self._keep_id = None

    def _open_backdoor(self, parent) -> None:
        if self._dialog_open:
            return
        self._dialog_open = True
        try:
            ok = prompt_unlock(parent, self.guard)
        finally:
            self._dialog_open = False
        if ok:
            self.unlock()
            if self.on_local_unlocked is not None:
                try:
                    self.on_local_unlocked()
                except Exception as e:
                    config.log(f'[lock] 本地解锁回调异常: {e}')

    def unlock(self) -> None:
        if not self._locked:
            return
        self._locked = False
        if self._keep_id is not None:
            try:
                self.root.after_cancel(self._keep_id)
            except Exception:
                pass
            self._keep_id = None
        for w in self.windows:
            try:
                w.destroy()
            except Exception:
                pass
        self.windows = []
        win32.keep_awake(False)
        if self.guard is not None:
            self.guard.set_active(False)
        if self.audio is not None:
            try:
                self.audio.restore()
            except Exception as e:
                config.log(f'[lock] 恢复声音失败: {e}')
        config.log('[lock] 已解除锁定')
