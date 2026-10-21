# -*- coding: utf-8 -*-
"""守护服务（LocalSystem）：

- 修复文件 ACL、持有防删除句柄；
- 备份/恢复数据库与设置；
- 在非管理员用户会话中启动/看护 SYSTEM 代理；
- 维护“禁用任务管理器”策略；
- 看护 Web 服务（LocalService）运行。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import time

from core import config, store
from . import acl, backup, paths, policy, scm, wts
from .fileguard import FileGuard
from .win_service import run_service


class GuardService:
    def __init__(self):
        self.data = paths.data_dir()
        self.install = paths.install_dir()
        self.exe = paths.installed_exe()
        self.fg = FileGuard([self.exe, self.data, self.data / 'data.db'])
        self._last_acl = 0.0
        self._last_backup = 0.0
        self._last_launch: dict[int, float] = {}

    def setup(self) -> None:
        config.log(f'[guard] 启动 data={self.data} install={self.install}')
        try:
            self.data.mkdir(parents=True, exist_ok=True)
            (self.data / 'runtime').mkdir(parents=True, exist_ok=True)
            (self.data / 'backup').mkdir(parents=True, exist_ok=True)
        except Exception as e:
            config.log(f'[guard] 创建目录失败: {e}')
        acl.apply_all(self.install, self.data, config.log)
        backup.restore_if_needed(self.data)
        self.fg.open_all()
        self._last_acl = time.time()
        self._last_backup = time.time()

    def run(self, stop_event) -> None:
        while not stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                config.log(f'[guard] tick 异常: {e}')
            stop_event.wait(1.0)

    def _tick(self) -> None:
        disabled = 1
        try:
            disabled = int(store.get_settings().get('disable_taskmgr', 1))
        except Exception:
            pass
        sessions = wts.list_active_sessions()
        admin_ids = {s['id'] for s in sessions if s['is_admin']}
        for s in sessions:
            sid, session = s.get('sid') or '', int(s['id'])
            if s['is_admin']:
                continue
            if sid:
                # 每轮都写一次，孩子改回 0 也会被守护服务恢复
                policy.set_taskmgr(sid, bool(disabled))
            if wts.is_agent_healthy(self.data, session, 10.0):
                continue
            age = wts.heartbeat_age(self.data, session)
            if age is not None and age > 10.0:
                pid = wts.read_agent_pid(self.data, session)
                if pid and wts.kill_agent_pid(pid, session, self.exe.name):
                    config.log(f'[guard] 结束无心跳代理 pid={pid} session={session}')
                wts.clear_heartbeat(self.data, session)
            last = self._last_launch.get(session, 0.0)
            if time.time() - last < 12.0:
                continue
            self._last_launch[session] = time.time()
            wts.launch_agent(session, self.data)

        # 清理已退出会话留下的 heartbeat（只清理非管理员会话）
        try:
            rt = self.data / 'runtime'
            for hb in rt.glob('agent_*.hb'):
                try:
                    sid = int(hb.stem.split('_', 1)[1])
                except Exception:
                    continue
                if sid in admin_ids:
                    continue
                if time.time() - hb.stat().st_mtime > 120:
                    wts.clear_heartbeat(self.data, sid)
        except Exception:
            pass

        now = time.time()
        if now - self._last_acl > 30:
            self._last_acl = now
            acl.apply_all(self.install, self.data, config.log)
        if now - self._last_backup > 10:
            self._last_backup = now
            backup.backup_all(self.data)
            try:
                self.fg._open(self.data / 'data.db')
            except Exception:
                pass

        # Web 服务被意外停止时拉起；手动停止守护服务时会先退出本循环
        st = scm.service_state(paths.WEB_SERVICE)
        if st is not None and st == 1:      # SERVICE_STOPPED
            config.log('[guard] Web 服务未运行，尝试启动')
            scm.start(paths.WEB_SERVICE)

    def stop(self) -> None:
        config.log('[guard] 正在停止')
        try:
            policy.clear_all()
        except Exception:
            pass
        try:
            scm.stop(paths.WEB_SERVICE)
        except Exception:
            pass
        try:
            wts.kill_agents(self.data)
        except Exception as e:
            config.log(f'[guard] 结束代理失败: {e}')
        self.fg.close()


def run_guard() -> int:
    svc = GuardService()
    return run_service(paths.GUARD_SERVICE, svc.setup, svc.run, svc.stop)
