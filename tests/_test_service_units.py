# -*- coding: utf-8 -*-
"""服务化强化的纯逻辑测试：数据目录、备份恢复、ACL 参数、服务命令、策略路径。

不启动服务/客户端，可在 Linux 上运行。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
import os
import sys
import tempfile
from pathlib import Path

DATA = tempfile.mkdtemp(prefix='pctl_svc_units_')
os.environ['PCTL_DATA_DIR'] = DATA
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, store  # noqa: E402
from service import acl, backup, paths, policy  # noqa: E402

# 测试使用临时数据库，不碰真实 HKCU 用量
store._reg_write_usage = lambda d, sec: None
store._reg_read_usage = lambda d: 0

ok = True


def check(name, cond, extra=''):
    global ok
    print(('  PASS  ' if cond else '  FAIL  ') + name + (('  -> ' + str(extra)) if extra and not cond else ''))
    if not cond:
        ok = False


def close_store():
    try:
        if store._conn is not None:
            store._conn.close()
    except Exception:
        pass
    store._conn = None


# 测试不写真实 HKLM
backup._write_hklm = lambda payload: None

print('=== 数据目录 ===')
check('PCTL_DATA_DIR 生效', Path(config.DATA_DIR).resolve() == Path(DATA).resolve(),
      config.DATA_DIR)
check('数据库在指定目录', config.DB_PATH.parent.resolve() == Path(DATA).resolve(),
      config.DB_PATH)

print('=== 备份 / 恢复 ===')
store.update_settings(admin_hash=store.hash_pwd('admin123'),
                      unlock_hash=store.hash_pwd('8888'),
                      quota_minutes=60, hotkey='Ctrl+Shift+F12',
                      disable_taskmgr=0)
store.add_used(30)
store.add_event('setup', 'service test')
check('备份成功', backup.backup_all(DATA) is None)   # backup_all 返回 None
backup_dir = Path(DATA) / 'backup'
check('数据库备份存在', (backup_dir / 'data.db').exists())
check('JSON 备份存在', (backup_dir / 'settings.json').exists())
check('JSON 不含密码哈希', 'admin_hash' not in (backup_dir / 'settings.json').read_text(encoding='utf-8'))

close_store()
try:
    config.DB_PATH.unlink()
except OSError:
    pass
check('数据库恢复', backup.restore_if_needed(DATA) is True)
s = store.get_settings()
check('恢复额度', int(s['quota_minutes']) == 60, s.get('quota_minutes'))
check('恢复任务管理器设置', int(s['disable_taskmgr']) == 0, s.get('disable_taskmgr'))
check('恢复用量', store.get_used() == 30, store.get_used())
check('恢复密码', store.verify_pwd('admin123', s['admin_hash']))

# 只有 JSON 时重建数据库（密码清空，需要重新设置）
close_store()
(backup_dir / 'data.db').unlink()
for p in (config.DB_PATH, Path(str(config.DB_PATH) + '-wal'),
          Path(str(config.DB_PATH) + '-shm')):
    try:
        p.unlink()
    except OSError:
        pass
check('JSON 恢复', backup.restore_if_needed(DATA) is True)
s2 = store.get_settings()
check('JSON 恢复额度', int(s2['quota_minutes']) == 60, s2.get('quota_minutes'))
check('JSON 恢复用量', store.get_used() == 30, store.get_used())
check('JSON 恢复后需要重设密码', store.is_initialized() is False)

print('=== ACL 参数 ===')
prog = ' '.join(acl.program_acl_args(Path(r'C:\Program Files\PCTimeControlLite')))
data = ' '.join(acl.data_acl_args(Path(r'C:\ProgramData\PCTimeControlLite')))
check('程序目录 SYSTEM/管理员', '*S-1-5-18' in prog and '*S-1-5-32-544' in prog)
check('程序目录 LocalService 只读执行', '*S-1-5-19:(OI)(CI)RX' in prog)
check('数据目录 LocalService 完全控制', '*S-1-5-19:(OI)(CI)F' in data)
check('程序目录普通用户只读执行', '*S-1-5-32-545:(OI)(CI)RX' in prog)
check('数据目录普通用户没有授权', '*S-1-5-32-545' not in data)
check('递归应用 ACL', '/T' in prog and '/T' in data)

print('=== 服务命令/路径 ===')
guard = paths.service_bin_path('service')
web = paths.service_bin_path('web')
agent = paths.agent_bin_path(3)
check('guard binPath', '--service' in guard and '--data-dir' in guard, guard)
check('web binPath', '--web' in web and '--data-dir' in web, web)
check('agent binPath', '--agent --session 3' in agent and '--data-dir' in agent, agent)
check('任务管理器策略路径', policy.policy_path('S-1-5-21-1-2-3').endswith(
    r'Software\Microsoft\Windows\CurrentVersion\Policies\System'))

print()
print('=== 全部通过 ===' if ok else '=== 存在失败项 ===')
sys.exit(0 if ok else 1)
