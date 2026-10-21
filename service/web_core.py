# -*- coding: utf-8 -*-
"""Web 核心服务（LocalService）：

状态机 + 内置 HTTP 服务。不含任何 UI，也不以 SYSTEM 身份暴露网络。
锁定/静音/锁屏由用户会话中的 SYSTEM 代理轮询数据库完成。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import threading
import time

from core import config, store
from web import server as web_server
from web.server import WebService
from . import paths
from .win_service import run_service

web_server.SERVICE_MODE = True


class CoreService:
    def __init__(self):
        self.web = WebService()
        self.settings: dict = {}
        self._cfg_ver = ''
        self.quota = 7200
        self.used = 0
        self.pending = 0
        self.remaining = 7200
        self._cur_date = store.today()
        self._written = 0
        self._last_web_try = 0.0

    # ------------------------------------------------------------ 生命周期

    def setup(self) -> None:
        config.log(f'[core] Web 核心启动 data={config.DATA_DIR}')
        self._start_web()

    def _start_web(self) -> bool:
        self._last_web_try = time.time()
        ok = self.web.start()
        if not ok:
            config.log(f'[core] Web 服务启动失败（端口 {self.web.port}），稍后重试')
        return ok

    def run(self, stop_event: threading.Event) -> None:
        self.settings = store.get_settings()
        self._cfg_ver = self.settings.get('updated_at', '')
        self.quota = max(60, int(self.settings['quota_minutes']) * 60)
        self.used = store.get_used()
        self._written = self.used
        self.remaining = max(0, self.quota - self.used)
        self._cur_date = store.today()

        while not stop_event.wait(1.0):
            try:
                self.tick()
            except Exception as e:
                config.log(f'[core] tick 异常: {e}')
            if self.web.httpd is None and time.time() - self._last_web_try > 30:
                self._start_web()
        self.web.stop()

    def stop(self) -> None:
        try:
            self.web.stop()
        except Exception:
            pass

    # ------------------------------------------------------------ 每秒

    def tick(self) -> None:
        s = store.get_settings()
        ver = s.get('updated_at', '')
        changed = ver != self._cfg_ver
        self._cfg_ver = ver
        self.settings = s
        self.quota = max(60, int(s['quota_minutes']) * 60)
        if changed:
            config.log(f'[core] 设置更新 quota={self.quota}s '
                       f'style={s.get("lock_style")} enabled={s["enabled"]} '
                       f'locked={s["locked"]}')
        self._reconcile_used()

        enabled = int(s['enabled']) == 1
        locked = int(s['locked']) == 1

        d = store.today()
        if d != self._cur_date:
            self._cur_date = d
            self.pending = 0
            self.used = store.get_used(d)
            self._written = self.used
            config.log(f'[core] 新的一天 {d}')

        if enabled and not locked:
            self.pending += 1
        if self.pending:
            self.used = store.add_used(self.pending)
            self._written = self.used
            self.pending = 0
        self.remaining = max(0, self.quota - self.used - self.pending)

        if enabled and not locked and self.remaining <= 0:
            store.update_settings(locked=1, lock_reason='quota')
            store.add_event('locked', '时间用完，通知代理进入锁定画面')
            config.log('[core] 时间用完，写入锁定状态')

        self.handle_commands()

    def _reconcile_used(self) -> None:
        db_used = store.get_used()
        if db_used + 2 < self._written:
            config.log(f'[core] 检测到外部重置：{self._written}s → {db_used}s')
            self.used = db_used
            self.pending = 0
            self._written = db_used
        elif db_used > self._written:
            self.used = db_used
            self._written = db_used

    # ------------------------------------------------------------ 指令/端口

    def handle_commands(self) -> None:
        # toast 留给 SYSTEM 代理；这里只处理服务侧指令
        for cmd in store.take_commands({'set_port', 'lock', 'unlock'}):
            t = cmd.get('type')
            pl = cmd.get('payload') or {}
            if t == 'set_port':
                self.apply_port(pl.get('port'))
            elif t == 'lock':
                store.update_settings(locked=1, lock_reason='manual')
            elif t == 'unlock':
                store.update_settings(locked=0, lock_reason=None)
                store.reset_used()

    def apply_port(self, port) -> tuple[bool, str]:
        try:
            p = int(port)
        except (TypeError, ValueError):
            return False, '端口必须是数字。'
        current = int(self.web.port or config.DEFAULT_PORT)
        if p == current and self.web.httpd is not None:
            return True, f'端口未变化（{p}）。'
        ok, msg = config.validate_port(p)
        if not ok:
            return False, msg
        old = current
        self.web.stop()
        if self.web.start(strict_port=p):
            store.add_event('port_changed', f'端口已切换到 {p}')
            config.log(f'[core] 端口已切换到 {p}')
            return True, f'已切换到端口 {p}。'
        self.web.start(strict_port=old)
        store.add_event('port_change_failed', f'端口 {p} 启动失败，已恢复 {old}')
        config.log(f'[core] 端口 {p} 启动失败，已恢复 {old}')
        return False, f'端口 {p} 启动失败，已恢复 {old}。'


def run_web_core() -> int:
    svc = CoreService()
    return run_service(paths.WEB_SERVICE, svc.setup, svc.run, svc.stop)
