# -*- coding: utf-8 -*-
"""内置 HTTP 服务：手机浏览器访问 http://电脑IP:8080 即可管控。

所有状态都存在 SQLite 里，主线程每秒同步一次 —— Web 线程只读写数据，不碰 Qt 对象。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import config, hotkey, netinfo, store
from web import pages

_sessions: dict[str, dict] = {}
_sess_lock = threading.Lock()
SESSION_TTL = 12 * 3600

#: 简单的登录失败限流：同一 IP 5 分钟内失败 5 次，暂停 60 秒
_login_fail: dict[str, list[float]] = {}
_login_lock = threading.Lock()
LOGIN_WINDOW = 300.0
LOGIN_MAX = 5
LOGIN_BLOCK = 60.0


def _login_blocked(ip: str) -> bool:
    now = time.time()
    with _login_lock:
        arr = [t for t in _login_fail.get(ip, []) if now - t <= LOGIN_WINDOW]
        _login_fail[ip] = arr
        return len(arr) >= LOGIN_MAX and (now - arr[-1]) < LOGIN_BLOCK


def _login_failed(ip: str) -> None:
    now = time.time()
    with _login_lock:
        arr = [t for t in _login_fail.get(ip, []) if now - t <= LOGIN_WINDOW]
        arr.append(now)
        _login_fail[ip] = arr[-20:]


def _login_ok(ip: str) -> None:
    with _login_lock:
        _login_fail.pop(ip, None)

#: 服务实际监听的端口（可能因不安全/被占用而与配置不同）
ACTUAL_PORT = 0

#: 是否为强化保护服务模式（决定网页是否显示任务管理器策略开关）
SERVICE_MODE = False


def _res_path(rel: str) -> Path:
    base = getattr(sys, '_MEIPASS', None)
    if base:
        return Path(base) / rel
    return Path(__file__).resolve().parent / rel


def new_session() -> str:
    tok = secrets.token_urlsafe(24)
    with _sess_lock:
        _sessions[tok] = {'exp': time.time() + SESSION_TTL, 'flash': ''}
    return tok


def get_session(tok: str | None) -> dict | None:
    if not tok:
        return None
    with _sess_lock:
        s = _sessions.get(tok)
        if not s:
            return None
        if s['exp'] < time.time():
            _sessions.pop(tok, None)
            return None
        return s


def drop_session(tok: str | None) -> None:
    if tok:
        with _sess_lock:
            _sessions.pop(tok, None)


def set_flash(tok: str | None, msg: str) -> None:
    s = get_session(tok)
    if s:
        s['flash'] = msg


def take_flash(tok: str | None) -> str:
    s = get_session(tok)
    if not s:
        return ''
    m = s.get('flash', '')
    s['flash'] = ''
    return m


class Handler(BaseHTTPRequestHandler):
    server_version = 'PCTimeLite'
    sys_version = ''

    # ---------------- 基础工具 ----------------

    def log_request(self, code='-', size='-'):
        """记录每个请求的来源 IP —— 手机连不上时靠它判断是网络层还是应用层问题。"""
        try:
            ip = self.client_address[0] if self.client_address else '?'
            config.log(f'[web] {ip} {self.command} {self.path} -> {code}')
        except Exception:
            pass

    def log_message(self, fmt, *args):  # 静音默认格式
        pass

    def _send(self, code: int, body: bytes, ctype: str = 'text/html; charset=utf-8',
              extra: list[tuple[str, str]] | None = None) -> None:
        try:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _redirect(self, to: str, extra: list[tuple[str, str]] | None = None) -> None:
        try:
            self.send_response(303)
            self.send_header('Location', to)
            self.send_header('Content-Length', '0')
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()
        except Exception:
            pass

    def _cookie_token(self) -> str | None:
        raw = self.headers.get('Cookie') or ''
        for part in raw.split(';'):
            part = part.strip()
            if part.startswith('pctlite_sid='):
                return part.split('=', 1)[1]
        return None

    def _form(self) -> dict:
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if n <= 0 or n > 64 * 1024:
                return {}
            body = self.rfile.read(n).decode('utf-8', 'replace')
            return {k: v[0] for k, v in urllib.parse.parse_qs(body, keep_blank_values=True).items()}
        except Exception:
            return {}

    def _same_origin(self) -> bool:
        """简易 CSRF 防护：POST 必须来自本站。"""
        host = (self.headers.get('Host') or '').lower()
        for hname in ('Origin', 'Referer'):
            v = self.headers.get(hname)
            if v:
                try:
                    netloc = urllib.parse.urlparse(v).netloc.lower()
                    return netloc == host
                except Exception:
                    return False
        return True  # 老客户端不带 Referer 时放行（家用局域网场景）

    def _authed(self) -> bool:
        return get_session(self._cookie_token()) is not None

    def _require(self) -> bool:
        if self._authed():
            return True
        self._redirect('/login')
        return False

    # ---------------- 路由 ----------------

    def do_GET(self):
        self._route('GET')

    def do_POST(self):
        self._route('POST')

    def _route(self, method: str):
        try:
            path = urllib.parse.urlparse(self.path).path
            if method == 'GET':
                self._get(path)
            else:
                self._post(path)
        except Exception as e:
            config.log(f'[web] {method} {self.path} 异常: {e}')
            self._send(500, pages.page_msg('出错了', '⚠️', '服务内部错误，请稍后再试。'))

    # ---------------- GET ----------------

    def _get(self, path: str):
        if path == '/static/app.css':
            try:
                data = _res_path('static/app.css').read_bytes()
            except Exception:
                try:
                    data = (_res_path('web/static/app.css')).read_bytes()
                except Exception:
                    data = b''
            self._send(200, data, 'text/css; charset=utf-8')
            return

        if path == '/favicon.ico':
            self._send(204, b'')
            return

        # 未初始化 -> 强制进设置向导
        if not store.is_initialized():
            if path == '/setup':
                self._send(200, pages.page_setup())
                return
            if path.startswith('/static'):
                self._send(404, b'')
                return
            self._redirect('/setup')
            return

        if path == '/login':
            if self._authed():
                self._redirect('/')
            else:
                self._send(200, pages.page_login())
            return

        if not self._require():
            return

        tok = self._cookie_token()
        flash = take_flash(tok)

        if path == '/':
            self._send(200, pages.page_home(self._state(), flash))
        elif path == '/settings':
            self._send(200, pages.page_settings(self._state(), flash))
        elif path == '/logs':
            s = store.get_settings()
            quota = int(s['quota_minutes']) * 60
            self._send(200, pages.page_logs(store.recent_usage(14), store.recent_events(120), quota))
        elif path == '/logout':
            drop_session(tok)
            self._redirect('/login', [('Set-Cookie', 'pctlite_sid=; Max-Age=0; Path=/')])
        else:
            self._send(404, pages.page_msg('页面不存在', '🔍', '这个地址没有内容。'))

    # ---------------- POST ----------------

    def _post(self, path: str):
        if not self._same_origin():
            self._send(403, pages.page_msg('请求被拒绝', '🚫', '来源校验失败。'))
            return

        form = self._form()

        # 首次设置（还没有密码时）
        if path == '/setup':
            if store.is_initialized():
                self._redirect('/')
                return
            a = (form.get('admin_pwd') or '').strip()
            u = (form.get('unlock_pwd') or '').strip()
            try:
                q = int(form.get('quota') or 120)
            except ValueError:
                q = 120
            if len(a) < 6 or len(u) < 4:
                self._send(400, pages.page_setup('管理密码至少 6 位，解锁密码至少 4 位。'))
                return
            store.update_settings(admin_hash=store.hash_pwd(a),
                                  unlock_hash=store.hash_pwd(u),
                                  quota_minutes=max(1, min(1440, q)),
                                  enabled=0, locked=0, lock_reason=None)
            store.add_event('setup', f'初始化完成，每日额度 {q} 分钟')
            tok = new_session()
            self._redirect('/', [('Set-Cookie', f'pctlite_sid={tok}; Path=/; HttpOnly; SameSite=Lax')])
            return

        if path == '/login':
            ip = self.client_address[0] if self.client_address else '?'
            if _login_blocked(ip):
                self._send(429, pages.page_msg('登录过于频繁', '⏳',
                                               '密码错误次数过多，请 1 分钟后再试。'))
                return
            s = store.get_settings()
            pwd = (form.get('pwd') or '')
            if s.get('admin_hash') and store.verify_pwd(pwd, s['admin_hash']):
                _login_ok(ip)
                tok = new_session()
                self._redirect('/', [('Set-Cookie', f'pctlite_sid={tok}; Path=/; HttpOnly; SameSite=Lax')])
            else:
                _login_failed(ip)
                self._send(401, pages.page_login('管理密码不正确'))
            return

        if not self._require():
            return
        tok = self._cookie_token()

        if path == '/start':
            # 开始新一轮管控：清零用时 + 启用 + 解除任何锁定
            store.reset_used()
            store.update_settings(enabled=1, locked=0, lock_reason=None)
            store.add_event('parent_start', '手机端点了「开始管控」')
            set_flash(tok, '已开始管控，倒计时从零开始。')
            self._redirect('/')

        elif path == '/pause':
            # 暂停管控：停止计时、解除锁定（家长自己在用电脑）
            store.update_settings(enabled=0, locked=0, lock_reason=None)
            store.add_event('parent_pause', '手机端点了「暂停管控」')
            set_flash(tok, '已暂停管控：不计时、不锁屏。')
            self._redirect('/')

        elif path == '/settings':
            try:
                q = int(form.get('quota') or 120)
            except ValueError:
                q = 120
            q = max(1, min(1440, q))
            style = 'popup' if form.get('style') == 'popup' else 'bsod'
            # 注意：这里不碰 enabled —— 启停只由首页的「开始/暂停管控」按钮控制，
            # 否则改个额度会把正在进行的管控顺手关掉。
            store.update_settings(quota_minutes=q, lock_style=style)
            set_flash(tok, '设置已保存，电脑端 1 秒内生效。')
            self._redirect('/settings')

        elif path == '/local':
            raw_hk = (form.get('hotkey') or '').strip()
            ok, canon = hotkey.normalize_hotkey(raw_hk)
            if not ok:
                self._send(400, pages.page_settings(self._state(), '', canon))
                return
            hide_tray = 1 if (form.get('hide_tray') or '') in ('1', 'on', 'true', 'yes') else 0
            disable_tm = 1 if (form.get('disable_taskmgr') or '') in ('1', 'on', 'true', 'yes') else 0
            store.update_settings(hotkey=canon, tray_hidden=hide_tray,
                                  disable_taskmgr=disable_tm)
            store.add_event('local_settings',
                            f'本机设置：快捷键 {canon}，托盘{"隐藏" if hide_tray else "显示"}，'
                            f'任务管理器{"禁用" if disable_tm else "允许"}')
            set_flash(tok, '本机设置已保存，电脑端 1 秒内生效。')
            self._redirect('/settings')

        elif path == '/port':
            raw = (form.get('port') or '').strip()
            current = ACTUAL_PORT or int(config.load_cfg().get('port', config.DEFAULT_PORT))
            try:
                p = int(raw)
            except ValueError:
                self._send(400, pages.page_settings(self._state(), '', '端口必须是数字。'))
                return
            if p == current:
                set_flash(tok, f'端口未变化（{p}）。')
                self._redirect('/settings')
                return
            ok, msg = config.validate_port(p)
            if not ok:
                self._send(400, pages.page_settings(self._state(), '', msg))
                return
            store.push_command('set_port', {'port': p})
            store.add_event('port_change_requested', f'手机端请求端口改为 {p}')
            self._send(200, pages.page_port_changed(
                p, netinfo.access_urls(p), current))

        elif path == '/password':
            u = (form.get('unlock_pwd') or '').strip()
            a = (form.get('admin_pwd') or '').strip()
            msgs = []
            if u:
                if len(u) < 4:
                    msgs.append('解锁密码至少 4 位')
                else:
                    store.update_settings(unlock_hash=store.hash_pwd(u))
                    msgs.append('解锁密码已更新')
            if a:
                if len(a) < 6:
                    msgs.append('管理密码至少 6 位')
                else:
                    store.update_settings(admin_hash=store.hash_pwd(a))
                    msgs.append('管理密码已更新')
            if not msgs:
                self._redirect('/settings')
                return
            store.add_event('setup', '；'.join(msgs))
            set_flash(tok, '；'.join(msgs))
            self._redirect('/settings')

        elif path == '/lock':
            store.update_settings(locked=1, lock_reason='manual')
            store.add_event('parent_lock', '手机端点了立即锁定')
            set_flash(tok, '已锁定电脑屏幕。')
            self._redirect('/')

        elif path == '/unlock':
            store.reset_used()
            store.update_settings(locked=0, lock_reason=None)
            store.bump_unlock()
            store.add_event('unlocked', '手机端解锁，今日用时已清零')
            set_flash(tok, '已解锁，今日计时重新开始。')
            self._redirect('/')

        elif path == '/reset':
            store.reset_used()
            store.update_settings(locked=0, lock_reason=None)
            store.add_event('parent_reset', '手机端重置今日用时')
            set_flash(tok, '今日已用时间已清零。')
            self._redirect('/')

        elif path == '/toast':
            text = (form.get('text') or '').strip()
            if text:
                store.push_command('toast', {'text': text[:120], 'seconds': 10, 'level': 'info'})
                store.add_event('parent_toast', text[:120])
                set_flash(tok, '提醒已发送到电脑。')
            self._redirect('/')

        else:
            self._send(404, pages.page_msg('操作不存在', '🔍', '无效的请求。'))

    # ---------------- 状态聚合 ----------------

    def _state(self) -> dict:
        s = store.get_settings()
        quota = int(s['quota_minutes']) * 60
        used = store.get_used()
        left = max(0, quota - used)
        pct = 100 if quota <= 0 else max(0, min(100, int(round(left * 100 / quota))))
        row = store.get_usage_row()
        port = ACTUAL_PORT or int(config.load_cfg().get('port', config.DEFAULT_PORT))
        return {
            'settings': s,
            'quota': quota,
            'used': used,
            'left': left,
            'pct': pct,
            'unlock_count': int(row.get('unlock_count') or 0),
            'enabled': int(s['enabled']) == 1,
            'locked': int(s['locked']) == 1,
            'lock_style': s.get('lock_style') or 'bsod',
            'urls': netinfo.access_urls(port),
            'port': port,
            'hotkey': s.get('hotkey') or config.DEFAULT_HOTKEY,
            'tray_hidden': int(s.get('tray_hidden') or 0),
            'service_mode': bool(SERVICE_MODE),
        }


class WebService:
    """后台 HTTP 服务封装。"""

    def __init__(self):
        cfg = config.load_cfg()
        self.want_port = int(cfg.get('port', config.DEFAULT_PORT) or 0)
        self.port = self.want_port
        self.bind = cfg.get('bind', '0.0.0.0')
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self, strict_port: int | None = None) -> bool:
        """启动 HTTP 服务。

        strict_port 不为 None 时只尝试该端口，失败返回 False（用于网页改端口）；
        否则沿用 config.pick_port 的自动回退逻辑。
        """
        global ACTUAL_PORT
        if strict_port is not None:
            try:
                port = int(strict_port)
            except (TypeError, ValueError):
                return False
            if not (1024 <= port <= 65535) or config.is_unsafe_port(port):
                config.log(f'[web] 拒绝监听端口 {port}（范围/不安全端口）')
                return False
            if not config.port_free(port):
                config.log(f'[web] 端口 {port} 已被占用')
                return False
        else:
            port = config.pick_port(self.want_port)
            if config.is_unsafe_port(self.want_port) or port != self.want_port:
                config.log(f'[web] 端口 {self.want_port} 不可用'
                           f'（浏览器不安全端口/已被占用），改用 {port}')
        try:
            ThreadingHTTPServer.allow_reuse_address = True
            self.httpd = ThreadingHTTPServer((self.bind, port), Handler)
            self.httpd.daemon_threads = True
            self.port = port
            self.want_port = port
            ACTUAL_PORT = port
            self.thread = threading.Thread(target=self.httpd.serve_forever,
                                           kwargs={'poll_interval': 0.5},
                                           daemon=True, name='WebService')
            self.thread.start()
            # 把实际端口写回配置，下次直接用
            try:
                cfg = config.load_cfg()
                if int(cfg.get('port') or 0) != port:
                    cfg['port'] = port
                    config.save_cfg(cfg)
            except Exception:
                pass
            config.log(f'[web] 已启动 http://{self.bind}:{port}')
            for u in netinfo.access_urls(port):
                config.log(f'[web] 手机可访问: {u}')
            return True
        except OSError as e:
            config.log(f'[web] 启动失败（端口 {port}）: {e}')
            try:
                if self.httpd:
                    self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None
            return False

    def stop(self) -> None:
        global ACTUAL_PORT
        httpd, thread = self.httpd, self.thread
        self.httpd = None
        self.thread = None
        if httpd:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        if thread is not None:
            try:
                thread.join(3.0)
            except Exception:
                pass
        ACTUAL_PORT = 0
