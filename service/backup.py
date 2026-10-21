# -*- coding: utf-8 -*-
"""数据库/设置备份与恢复。

- 备份数据库到 ProgramData 下受 ACL 保护的 backup 目录；
- 导出一份不含密码哈希的 JSON，同时写入 HKLM（SYSTEM 可写，标准用户不可写）；
- 启动时发现数据库缺失/损坏则自动恢复。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from core import config
from . import paths

_IS_WINDOWS = os.name == 'nt'
BACKUP_DIRNAME = 'backup'
DB_BACKUP = 'data.db'
JSON_BACKUP = 'settings.json'


def _db_path(data: Path) -> Path:
    return Path(data) / 'data.db'


def _backup_dir(data: Path) -> Path:
    d = Path(data) / BACKUP_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def integrity_ok(db: Path) -> bool:
    try:
        c = sqlite3.connect(str(db), timeout=5)
        try:
            row = c.execute('PRAGMA integrity_check').fetchone()
            return bool(row and str(row[0]).lower() == 'ok')
        finally:
            c.close()
    except Exception:
        return False


def backup_database(data: Path) -> bool:
    db = _db_path(data)
    if not db.exists():
        return False
    target = _backup_dir(data) / DB_BACKUP
    tmp = target.with_suffix(target.suffix + f'.{os.getpid()}.tmp')
    try:
        src = sqlite3.connect(str(db), timeout=10)
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        os.replace(tmp, target)
        return True
    except Exception as e:
        config.log(f'[backup] 数据库备份失败: {e}')
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def export_json(data: Path) -> dict | None:
    db = _db_path(data)
    if not db.exists():
        return None
    try:
        c = sqlite3.connect(str(db), timeout=10)
        c.row_factory = sqlite3.Row
        try:
            row = c.execute('SELECT * FROM settings WHERE id=1').fetchone()
            settings = dict(row) if row else {}
            # 密码哈希只留在受 ACL 保护的数据库/数据库备份里，不进 HKLM
            for k in ('admin_hash', 'unlock_hash'):
                settings.pop(k, None)
            usage = [dict(r) for r in c.execute(
                'SELECT stat_date, used_seconds, unlock_count, updated_at FROM usage '
                'ORDER BY stat_date DESC LIMIT 400').fetchall()]
        finally:
            c.close()
        payload = {
            'version': config.APP_VER,
            'saved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'settings': settings,
            'usage': usage,
        }
        target = _backup_dir(data) / JSON_BACKUP
        tmp = target.with_suffix(target.suffix + f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, target)
        _write_hklm(payload)
        return payload
    except Exception as e:
        config.log(f'[backup] 设置备份失败: {e}')
        return None


def _write_hklm(payload: dict) -> None:
    if not _IS_WINDOWS:
        return
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, paths.HKLM_ROOT, 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, 'BackupJson', 0, winreg.REG_SZ,
                              json.dumps(payload, ensure_ascii=False))
            winreg.SetValueEx(k, 'LastBackup', 0, winreg.REG_SZ,
                              payload.get('saved_at', ''))
    except Exception as e:
        config.log(f'[backup] 写 HKLM 备份失败: {e}')


def _read_hklm() -> dict | None:
    if not _IS_WINDOWS:
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, paths.HKLM_ROOT, 0,
                            winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, 'BackupJson')
            d = json.loads(val)
            return d if isinstance(d, dict) else None
    except Exception:
        return None


def _restore_from_payload(payload: dict, data: Path) -> bool:
    """没有数据库备份时，用 JSON 在受保护目录里重建数据库（不含密码）。"""
    try:
        os.environ['PCTL_DATA_DIR'] = str(data)
        from core import store
        store.db()
        s = payload.get('settings') or {}
        allowed = {k: s[k] for k in (
            'quota_minutes', 'warn_minutes', 'enabled', 'locked', 'lock_reason',
            'lock_style', 'bsod_code', 'hotkey', 'tray_hidden', 'disable_taskmgr',
        ) if k in s}
        if allowed:
            store.update_settings(**allowed)
        rows = payload.get('usage') or []
        if rows:
            conn = store.db()
            for r in rows:
                try:
                    conn.execute(
                        'INSERT INTO usage (stat_date, used_seconds, unlock_count, updated_at) '
                        'VALUES (?,?,?,?) ON CONFLICT(stat_date) DO UPDATE SET '
                        'used_seconds=excluded.used_seconds, '
                        'unlock_count=excluded.unlock_count, updated_at=excluded.updated_at',
                        (str(r.get('stat_date')), int(r.get('used_seconds') or 0),
                         int(r.get('unlock_count') or 0),
                         str(r.get('updated_at') or time.strftime('%Y-%m-%d %H:%M:%S'))))
                except Exception:
                    pass
            conn.commit()
        config.log('[backup] 已从 JSON/HKLM 备份恢复设置与用量（密码需重新设置）')
        return True
    except Exception as e:
        config.log(f'[backup] JSON 恢复失败: {e}')
        return False


def restore_if_needed(data: Path) -> bool:
    """数据库缺失或损坏时恢复。返回是否执行了恢复。"""
    data = Path(data)
    db = _db_path(data)
    need = (not db.exists()) or (db.stat().st_size == 0) or (not integrity_ok(db))
    if not need:
        return False
    config.log('[backup] 数据库缺失或损坏，开始恢复')
    try:
        data.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if db.exists():
        try:
            os.replace(db, db.with_suffix('.db.corrupt'))
        except Exception:
            try:
                db.unlink()
            except OSError:
                pass
    for suffix in ('-wal', '-shm'):
        try:
            Path(str(db) + suffix).unlink()
        except OSError:
            pass

    bak = _backup_dir(data) / DB_BACKUP
    if bak.exists() and bak.stat().st_size > 0 and integrity_ok(bak):
        try:
            shutil.copy2(bak, db)
            config.log('[backup] 已从数据库备份恢复')
            return True
        except Exception as e:
            config.log(f'[backup] 复制数据库备份失败: {e}')

    payload = None
    try:
        p = _backup_dir(data) / JSON_BACKUP
        if p.exists():
            payload = json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        payload = None
    if not payload:
        payload = _read_hklm()
    if payload and _restore_from_payload(payload, data):
        return True
    config.log('[backup] 没有可用备份，使用空数据库')
    return False


def backup_all(data: Path) -> None:
    backup_database(data)
    export_json(data)
