# -*- coding: utf-8 -*-
"""提醒横幅：置顶、不抢焦点、自动消失。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import tkinter as tk

from ui import win32

FONT_CN = 'Microsoft YaHei UI'


class Toast(tk.Toplevel):
    def __init__(self, master, text: str, seconds: int = 15, level: str = 'info'):
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        try:
            self.attributes('-alpha', 0.96)
        except Exception:
            pass

        warn = (level == 'warn')
        bg = '#d66014' if warn else '#121a2a'
        border = '#ffb05a' if warn else '#5a8cf0'
        self.configure(bg=border)

        frame = tk.Frame(self, bg=bg, highlightbackground=border,
                         highlightthickness=1)
        frame.pack(fill='both', expand=True, padx=1, pady=1)

        icon = tk.Label(frame, text=('📢' if warn else '⏰'), bg=bg, fg='#ffffff',
                        font=(FONT_CN, 24))
        icon.pack(side='left', padx=(24, 10), pady=18)
        msg = tk.Label(frame, text=text, bg=bg, fg='#ffffff',
                       font=(FONT_CN, 15, 'bold'), wraplength=620,
                       justify='left')
        msg.pack(side='left', padx=(0, 26), pady=20)

        self.update_idletasks()
        w = min(760, max(280, self.winfo_reqwidth()))
        h = max(64, self.winfo_reqheight())
        px, py, pw, _ph = win32.primary_rect()
        x = max(px, px + (pw - w) // 2)
        y = max(py, py + 70)
        self.geometry(f'{w}x{h}+{x}+{y}')

        for wdg in (self, frame, icon, msg):
            wdg.bind('<Button-1>', lambda e: self.close())

        self._closed = False
        self._after_id = self.after(max(3, int(seconds)) * 1000, self.close)
        win32.show_no_activate(self)
        self.lift()
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.after_cancel(self._after_id)
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


class ToastManager:
    """同一时刻只保留一条提醒，新提醒顶掉旧的。"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self._cur: Toast | None = None

    def show(self, text: str, seconds: int = 15, level: str = 'info') -> None:
        if self._cur is not None:
            try:
                self._cur.close()
            except Exception:
                pass
            self._cur = None
        try:
            self._cur = Toast(self.root, text, seconds, level)
        except Exception as e:
            from core import config
            config.log(f'[toast] 显示失败: {e}')
            self._cur = None
