# -*- coding: utf-8 -*-
"""服务安装/卸载（需管理员）。

安装：
  - 复制 exe 到 Program Files；
  - 迁移便携数据到 ProgramData；
  - 设置 ACL；
  - 创建 PCTimeGuard（LocalSystem）与 PCTimeWeb（LocalService）服务；
  - 添加入站防火墙规则。

卸载：
  - 校验解锁/管理密码；
  - 停止/删除服务、结束代理；
  - 清理任务管理器策略、ACL、防火墙、注册表和文件。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from core import config
from . import acl, paths, policy, scm, wts

_IS_WINDOWS = os.name == 'nt'
CREATE_NO_WINDOW = 0x08000000
MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
MB_YESNO = 0x4
MB_ICONERROR = 0x10
MB_ICONINFORMATION = 0x40
MB_ICONQUESTION = 0x20
IDYES = 6


def _log(msg: str) -> None:
    try:
        config.log(msg)
    except Exception:
        pass
    try:
        p = Path(tempfile.gettempdir()) / 'PCTimeControlLite-install.log'
        with open(p, 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + str(msg) + '\n')
    except Exception:
        pass


def _msg(title: str, text: str, flags: int = MB_ICONINFORMATION) -> int:
    if not _IS_WINDOWS:
        print(f'[{title}] {text}')
        return 1
    try:
        return int(ctypes.windll.user32.MessageBoxW(None, text, title, flags))
    except Exception:
        return 1


def _ask_password(prompt: str) -> str | None:
    try:
        import tkinter as tk
        from tkinter import simpledialog
        root = tk.Tk()
        root.withdraw()
        val = simpledialog.askstring('确认', prompt, show='*', parent=None)
        root.destroy()
        return val
    except Exception as e:
        _log(f'[install] 密码框失败: {e}')
        return None


def is_admin() -> bool:
    if not _IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _elevate() -> bool:
    if not _IS_WINDOWS:
        return False
    try:
        params = subprocess.list2cmdline(sys.argv[1:])
        rc = ctypes.windll.shell32.ShellExecuteW(None, 'runas', sys.executable,
                                                 params, None, 1)
        return int(rc) > 32
    except Exception as e:
        _log(f'[install] 提权失败: {e}')
        return False


def _run(args: list[str]) -> bool:
    if not _IS_WINDOWS:
        return False
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW, timeout=90)
        if p.returncode != 0:
            _log(f'[install] 命令失败 {p.returncode}: {args} :: '
                 f'{(p.stdout or "")[-400:]} {(p.stderr or "")[-400:]}')
        return p.returncode == 0
    except Exception as e:
        _log(f'[install] 命令异常 {args}: {e}')
        return False


def _remove_dir(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _schedule_delete(path: Path) -> None:
    if not _IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                                         ctypes.c_ulong]
        kernel32.MoveFileExW.restype = ctypes.c_int
        kernel32.MoveFileExW(str(path), None, MOVEFILE_DELAY_UNTIL_REBOOT)
        _log(f'[install] 已计划重启后删除 {path}')
    except Exception as e:
        _log(f'[install] 计划删除失败 {path}: {e}')


def _stop_everything(data: Path) -> bool:
    ok = True
    try:
        scm.stop(paths.GUARD_SERVICE)
        ok = scm.wait_stopped(paths.GUARD_SERVICE, 25.0) and ok
    except Exception:
        pass
    try:
        scm.stop(paths.WEB_SERVICE)
        ok = scm.wait_stopped(paths.WEB_SERVICE, 15.0) and ok
    except Exception:
        pass
    time.sleep(0.5)
    try:
        wts.kill_agents(data)
    except Exception as e:
        _log(f'[install] 结束代理失败: {e}')
    return ok


def _migrate_data(target: Path) -> None:
    if (target / 'data.db').exists():
        _log('[install] 目标数据目录已有数据库，跳过迁移')
        return
    candidates: list[Path] = []
    for base in (os.environ.get('LOCALAPPDATA'), os.environ.get('APPDATA')):
        if base:
            candidates.append(Path(base) / config.APP_NAME / 'data.db')
    try:
        users = Path(os.environ.get('SystemDrive', 'C:') + '\\Users')
        for p in users.glob(f'*/AppData/Local/{config.APP_NAME}/data.db'):
            candidates.append(p)
        for p in users.glob(f'*/AppData/Roaming/{config.APP_NAME}/data.db'):
            candidates.append(p)
    except Exception:
        pass
    existing = [p for p in candidates if p.exists()]
    if not existing:
        _log('[install] 未找到可迁移的便携数据')
        return
    src = max(existing, key=lambda p: p.stat().st_mtime)
    _log(f'[install] 从 {src} 迁移数据')
    for name in ('data.db', 'data.db-wal', 'data.db-shm', 'config.json', 'audio.state'):
        s = src.parent / name
        if s.exists():
            try:
                shutil.copy2(s, target / name)
            except Exception as e:
                _log(f'[install] 迁移 {name} 失败: {e}')


def _remove_old_exe(target: Path) -> None:
    """重装时若旧安装的 exe 名称不同，删除旧文件。"""
    if not _IS_WINDOWS:
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, paths.HKLM_ROOT, 0,
                            winreg.KEY_READ) as k:
            old_dir = Path(str(winreg.QueryValueEx(k, 'InstallDir')[0]))
            old_name = str(winreg.QueryValueEx(k, 'InstallExe')[0])
        old = old_dir / old_name
        if old.exists() and old.resolve() != target.resolve():
            try:
                old.unlink()
                _log(f'[install] 已删除旧 exe {old}')
            except Exception:
                _schedule_delete(old)
    except Exception:
        pass


def _read_port() -> int:
    try:
        cfg = json.loads((paths.data_dir() / 'config.json').read_text(encoding='utf-8'))
        return int(cfg.get('port') or config.DEFAULT_PORT)
    except Exception:
        return config.DEFAULT_PORT


def _set_hklm(install: Path, data: Path, port: int, exe_name: str) -> None:
    if not _IS_WINDOWS:
        return
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, paths.HKLM_ROOT, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_READ) as k:
            winreg.SetValueEx(k, 'Installed', 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, 'InstallDir', 0, winreg.REG_SZ, str(install))
            winreg.SetValueEx(k, 'DataDir', 0, winreg.REG_SZ, str(data))
            winreg.SetValueEx(k, 'InstallExe', 0, winreg.REG_SZ, str(exe_name))
            winreg.SetValueEx(k, 'Version', 0, winreg.REG_SZ, config.APP_VER)
            winreg.SetValueEx(k, 'Port', 0, winreg.REG_DWORD, int(port))
    except Exception as e:
        _log(f'[install] 写 HKLM 失败: {e}')


def _remove_hklm() -> None:
    if not _IS_WINDOWS:
        return
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, paths.HKLM_ROOT)
    except Exception:
        pass


def _remove_run_key() -> None:
    """清理当前用户、已加载用户及磁盘用户 hive 里的便携自启项。"""
    if not _IS_WINDOWS:
        return
    import winreg
    run = r'Software\Microsoft\Windows\CurrentVersion\Run'

    def _del(root, subkey=run):
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, config.APP_NAME)
        except Exception:
            pass

    _del(winreg.HKEY_CURRENT_USER)
    loaded: set[str] = set()
    try:
        with winreg.OpenKey(winreg.HKEY_USERS, '', 0, winreg.KEY_READ) as root:
            i = 0
            while True:
                try:
                    sid = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if sid.startswith('S-1-'):
                    loaded.add(sid)
                    _del(winreg.HKEY_USERS, f'{sid}\\{run}')
    except Exception:
        pass

    # 未登录用户的 Run 键仍在 NTUSER.DAT 里
    try:
        from .policy import _lookup_sid
        from .wts import _enable_privilege
        _enable_privilege('SeRestorePrivilege')
        _enable_privilege('SeBackupPrivilege')
        users = Path(os.environ.get('SystemDrive', 'C:') + '\\Users')
        for prof in users.iterdir():
            if not prof.is_dir() or prof.name in ('Public', 'Default', 'Default User', 'All Users'):
                continue
            hive = prof / 'NTUSER.DAT'
            if not hive.exists():
                continue
            sid = _lookup_sid(prof.name)
            if not sid or sid in loaded:
                continue
            try:
                winreg.LoadKey(winreg.HKEY_USERS, sid, str(hive))
            except Exception:
                continue
            try:
                _del(winreg.HKEY_USERS, f'{sid}\\{run}')
            finally:
                try:
                    winreg.UnloadKey(winreg.HKEY_USERS, sid)
                except Exception:
                    pass
    except Exception:
        pass


def _firewall_add(exe: Path) -> None:
    _run(['netsh', 'advfirewall', 'firewall', 'delete', 'rule',
          f'name={paths.FIREWALL_RULE}'])
    _run(['netsh', 'advfirewall', 'firewall', 'add', 'rule',
          f'name={paths.FIREWALL_RULE}', 'dir=in', 'action=allow',
          f'program="{exe}"', 'enable=yes', 'profile=private,domain'])


def _firewall_remove() -> None:
    _run(['netsh', 'advfirewall', 'firewall', 'delete', 'rule',
          f'name={paths.FIREWALL_RULE}'])


def _check_frozen() -> bool:
    if getattr(sys, 'frozen', False):
        return True
    _msg('无法安装', '请先用 build.bat 打包成 exe，再运行 --install。', MB_ICONERROR)
    return False


def _initial_setup_if_needed() -> bool:
    """安装时由管理员先设置密码，避免孩子在首次登录时抢先设置。"""
    try:
        from core import store
        if store.is_initialized():
            return True
    except Exception as e:
        _log(f'[install] 检查初始化状态失败: {e}')
    try:
        import tkinter as tk
        from ui.panel import SetupWindow
        root = tk.Tk()
        root.withdraw()
        state = {'ok': False}

        def done():
            try:
                from core import store as st
                state['ok'] = st.is_initialized()
            except Exception:
                state['ok'] = False
            try:
                root.quit()
            except Exception:
                pass

        def cancel():
            state['ok'] = False
            try:
                root.quit()
            except Exception:
                pass

        win = SetupWindow(root, done, cancel)
        root.mainloop()
        try:
            win.destroy()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass
        if not state['ok']:
            _msg('安装已取消', '未完成管理/解锁密码设置，安装中止。', MB_ICONERROR)
        return bool(state['ok'])
    except Exception as e:
        _log(f'[install] 首次设置窗口失败: {e}')
        _msg('安装失败', f'无法打开首次设置窗口：\n{e}', MB_ICONERROR)
        return False


def install() -> int:
    if not _IS_WINDOWS:
        return 1
    if not is_admin():
        if not _elevate():
            _msg('安装失败', '需要管理员权限。', MB_ICONERROR)
        return 0
    if not _check_frozen():
        return 1
    if _msg('安装强化保护',
            '将把程序安装到 Program Files，并创建两个 Windows 服务：\n'
            '  PCTimeGuard（SYSTEM 守护）\n'
            '  PCTimeWeb（LocalService 网页/状态）\n\n'
            '孩子的标准用户会话会启动 SYSTEM 代理，普通用户无法结束或删除。\n'
            '需要管理员才能卸载。继续吗？',
            MB_YESNO | MB_ICONQUESTION) != IDYES:
        return 0

    install_dir = paths.install_dir()
    data_dir = paths.data_dir()
    source = Path(sys.executable).resolve()
    target = install_dir / source.name
    _log(f'[install] 开始安装 source={source} target={target} data={data_dir}')

    if not _stop_everything(data_dir):
        _msg('安装失败', '旧服务无法停止，请重启电脑后再安装。', MB_ICONERROR)
        return 1
    try:
        scm.delete_service(paths.WEB_SERVICE)
        scm.delete_service(paths.GUARD_SERVICE)
    except Exception:
        pass
    time.sleep(1.0)

    try:
        install_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        _log(f'[install] 建目录失败: {e}')
    _remove_old_exe(target)
    try:
        if source != target:
            shutil.copy2(source, target)
    except Exception as e:
        _msg('安装失败', f'复制程序失败：\n{e}', MB_ICONERROR)
        return 1
    _migrate_data(data_dir)
    acl.apply_all(install_dir, data_dir, _log)
    if not _initial_setup_if_needed():
        return 1

    port = _read_port()
    _set_hklm(install_dir, data_dir, port, target.name)

    guard_bin = f'"{target}" --service --data-dir "{data_dir}"'
    web_bin = f'"{target}" --web --data-dir "{data_dir}"'
    ok_guard = scm.create_service(paths.GUARD_SERVICE, paths.GUARD_DISPLAY,
                                  guard_bin, account=None)
    ok_web = scm.create_service(paths.WEB_SERVICE, paths.WEB_DISPLAY,
                                web_bin, account='NT AUTHORITY\\LocalService',
                                dependency=paths.GUARD_SERVICE)
    if not (ok_guard and ok_web):
        time.sleep(1.0)
        ok_guard = ok_guard or scm.create_service(
            paths.GUARD_SERVICE, paths.GUARD_DISPLAY, guard_bin, account=None)
        ok_web = ok_web or scm.create_service(
            paths.WEB_SERVICE, paths.WEB_DISPLAY, web_bin,
            account='NT AUTHORITY\\LocalService',
            dependency=paths.GUARD_SERVICE)
    if not (ok_guard and ok_web):
        _msg('安装失败', '创建服务失败，请查看 %TEMP%\\PCTimeControlLite-install.log。',
             MB_ICONERROR)
        return 1

    _firewall_add(target)
    _remove_run_key()
    scm.start(paths.GUARD_SERVICE)
    time.sleep(1.0)
    scm.start(paths.WEB_SERVICE)
    time.sleep(2.0)
    if not scm.is_running(paths.GUARD_SERVICE):
        _msg('服务未启动',
             '守护服务未能启动。请查看：\n'
             f'{data_dir}\\client.log\n%TEMP%\\PCTimeControlLite-install.log',
             MB_ICONERROR)
        return 1
    if not scm.is_running(paths.WEB_SERVICE):
        _log('[install] Web 服务尚未运行，守护服务会继续尝试启动')
    _log('[install] 安装完成')
    _msg('安装完成',
         f'强化保护已启用。\n\n手机访问：http://电脑IP:{port}\n\n'
         '孩子登录标准用户后会自动启动 SYSTEM 代理；管理员账户不受锁屏影响。\n'
         '卸载请以管理员身份运行：\n'
         f'"{target}" --uninstall',
         MB_ICONINFORMATION)
    return 0


def _verify_password(data: Path) -> bool:
    if '--force' in sys.argv:
        return True
    db = data / 'data.db'
    if not db.exists():
        return True
    admin_hash = unlock_hash = ''
    try:
        c = sqlite3.connect(str(db), timeout=5)
        try:
            row = c.execute('SELECT admin_hash, unlock_hash FROM settings WHERE id=1').fetchone()
            if row:
                admin_hash, unlock_hash = str(row[0] or ''), str(row[1] or '')
        finally:
            c.close()
    except Exception as e:
        _log(f'[uninstall] 读取密码哈希失败: {e}')
        return True
    if not admin_hash and not unlock_hash:
        return True
    pwd = _ask_password('请输入管理密码或解锁密码以卸载：')
    if pwd is None:
        return False
    try:
        from core import store
        return bool((admin_hash and store.verify_pwd(pwd, admin_hash)) or
                    (unlock_hash and store.verify_pwd(pwd, unlock_hash)))
    except Exception:
        return False


def _delete_installed_exe(target: Path) -> None:
    try:
        running = Path(sys.executable).resolve()
    except Exception:
        running = None
    if running and running == target.resolve():
        _schedule_delete(target)
    else:
        try:
            target.unlink()
        except Exception:
            _schedule_delete(target)
    try:
        target.parent.rmdir()
    except Exception:
        pass


def _remove_portable_data() -> None:
    candidates: list[Path] = []
    for base in (os.environ.get('LOCALAPPDATA'), os.environ.get('APPDATA')):
        if base:
            candidates.append(Path(base) / config.APP_NAME)
    try:
        users = Path(os.environ.get('SystemDrive', 'C:') + '\\Users')
        candidates += list(users.glob(f'*/AppData/Local/{config.APP_NAME}'))
        candidates += list(users.glob(f'*/AppData/Roaming/{config.APP_NAME}'))
    except Exception:
        pass
    for p in candidates:
        if p.exists() and p.resolve() != paths.data_dir().resolve():
            _log(f'[uninstall] 删除便携数据 {p}')
            _remove_dir(p)


def uninstall() -> int:
    if not _IS_WINDOWS:
        return 1
    if not is_admin():
        if not _elevate():
            _msg('卸载失败', '需要管理员权限。', MB_ICONERROR)
        return 0
    install_dir = paths.install_dir()
    data_dir = paths.data_dir()
    target = paths.installed_exe()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, paths.HKLM_ROOT, 0,
                            winreg.KEY_READ) as k:
            name = str(winreg.QueryValueEx(k, 'InstallExe')[0])
            if name:
                target = install_dir / name
    except Exception:
        pass
    if not _verify_password(data_dir):
        _msg('已取消', '密码不正确，未卸载。', MB_ICONERROR)
        return 1
    _log(f'[uninstall] 开始卸载 install={install_dir} data={data_dir}')

    if not _stop_everything(data_dir):
        _msg('卸载失败', '服务无法停止，请重启电脑后再卸载。', MB_ICONERROR)
        return 1
    try:
        scm.stop(paths.GUARD_SERVICE)
        scm.stop(paths.WEB_SERVICE)
        time.sleep(1.5)
        scm.delete_service(paths.WEB_SERVICE)
        scm.delete_service(paths.GUARD_SERVICE)
    except Exception as e:
        _log(f'[uninstall] 删除服务失败: {e}')
    try:
        wts.kill_agents(data_dir)
    except Exception:
        pass
    _firewall_remove()
    try:
        policy.clear_all()
    except Exception:
        pass
    acl.reset_all(install_dir, data_dir, _log)
    _remove_dir(data_dir)
    _remove_hklm()
    _remove_run_key()
    _remove_portable_data()
    _delete_installed_exe(target)
    _log('[uninstall] 卸载完成')
    _msg('卸载完成',
         '服务、防火墙规则、任务管理器策略和数据已清理。\n'
         '如果程序正在运行，exe 会在下次重启后删除。',
         MB_ICONINFORMATION)
    return 0
