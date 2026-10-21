# 开发与维护说明

本项目是 **Windows 单机应用**：Tkinter + Win32 ctypes，运行时零第三方 Python 依赖。更深入的架构说明见 [CLAUDE.md](CLAUDE.md)，用户文档见 [README.md](README.md) 和 [使用说明.md](使用说明.md)。

## 环境

- Windows 10/11（完整功能只支持 Windows）
- Python 3.10+（推荐 3.12），安装时保留 **Tcl/Tk**
- 开发/打包依赖：

```bat
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` 只包含 PyInstaller 和 Ruff；**运行程序本身不需要安装任何第三方包**。

也可以使用 uv：

```bat
uv sync
uv pip install -r requirements-dev.txt
uv run python main.py
```

## 运行

```bat
python main.py
```

首次运行会要求设置管理密码、解锁密码和每日额度。数据默认在 `%LOCALAPPDATA%\PCTimeControlLite\`。

## 打包

```bat
build.bat
```

产物：`dist\PCTimeControlLite.exe`。

关键参数见 `build.bat`：`--onefile --noconsole --collect-submodules service`，并带上 `web\static` 与 `app.ico`。新增静态资源时必须同步加到 `--add-data`。

## 测试

```powershell
powershell -ExecutionPolicy Bypass -File tests\run_all.ps1
```

**测试会清空 `%LOCALAPPDATA%\PCTimeControlLite` 和 `HKCU\Software\PCTimeControlLite`，并真实触发锁屏。** 请只在测试机或已备份数据的机器上运行。

- 测试期间会使用固定测试密码 `admin123` / `888888`，结束后脚本会停止客户端、保存日志到 `tests\last_run.log` 并清理测试数据和密码。
- 测试顺序有依赖：`_test_web_lite.py` 完成的首次设置供 `_test_e2e_lite.py` 使用。
- 六个脚本的覆盖范围见 README「开发与测试」。
- 服务模式的 SCM 服务、ACL、SYSTEM 会话代理无法由 `run_all.ps1` 覆盖，需要在 Windows 上手工 `--install` 验收。

Linux 上可运行纯逻辑测试；Tk 相关测试可配合 `xvfb-run`：

```bash
python tests/_test_units_lite.py
python tests/_test_service_units.py
xvfb-run -a python tests/_test_ui_lite.py
```

## 服务模式开发

- 安装/卸载只支持已打包的 exe：`PCTimeControlLite.exe --install` / `--uninstall`（管理员）。
- 内部模式：`--service`（PCTimeGuard）、`--web`（PCTimeWeb）、`--agent --session N`。
- 数据目录由 `--data-dir` 在导入 `core.config` 之前决定（见 `main._early_args`），不要绕过。
- 修改服务相关代码后，至少检查：`service/guard.py`、`web_core.py`、`agent.py`、`install.py`、`wts.py`、`scm.py`、`acl.py`、`backup.py`、`policy.py`。
- 不要在标准用户会话里直接调试 `--agent` 的权限行为；真实 SYSTEM 代理只能通过安装后的守护服务启动。

## 代码结构

```
main.py          便携模式 + 模式分派（--install/--service/--web/--agent）
core/            状态、存储、配置、音频、热键、连点、键盘钩子
ui/              Tk 托盘/锁屏/面板/提醒 + Win32 ctypes
web/             内置 HTTP 服务与页面
service/         强化保护服务模式（Guard/Web/Agent/安装器）
tests/           回归测试（脚本，不是 pytest）
docs/            设计文档与界面预览
```

关键约束：

- **Tk 只能在主线程调用**；Web/托盘/热键线程通过 `queue.Queue` 回传事件。
- 锁屏期间键盘钩子吞掉所有按键；密码框期间 `set_typing(True)` 放行普通字符。
- 便携模式用 `%LOCALAPPDATA%`；服务模式用 `ProgramData` + ACL，不能把 Web 服务改成 SYSTEM。
- 不要引入内核驱动/rootkit；本项目的保护边界是用户态 ACL + SYSTEM 服务/代理。
- 所有源文件保留作者署名 / MIT 声明；注释和界面文案使用中文。
- `.ps1` 必须保存为 UTF-8 with BOM（PowerShell 5.1）。

## 新增设置 / 功能时的检查清单

1. **存储**：`core/store.py` 的 `SCHEMA`、`_MIGRATIONS`、`get_settings` 默认值、`update_settings` 白名单。
2. **Web**：`web/server.py` 路由 + `web/pages.py` 表单；POST 需要同源校验/登录；写 DB 后主线程 1 秒内应用。
3. **本地面板**：`ui/panel.py` 控件 + `_sync_fields`；在 `main.App` 和 `service/agent.py` 实现对应方法并加入 `snapshot()`。
4. **生效逻辑**：便携模式在 `main.App.tick`/`_sync_settings`；服务模式在 `web_core.CoreService.tick` 或 `agent.AgentApp._poll`。
5. **测试**：纯逻辑加 `tests/_test_units_lite.py` / `_test_service_units.py`，UI 加 `_test_ui_lite.py`，必要时加 Web 测试。
6. **文档**：README、使用说明、CHANGELOG；涉及架构时更新 CLAUDE.md。

## 版本与发布

版本号需要同步：

- `core/config.py` 的 `APP_VER`
- `version_info.txt`
- `pyproject.toml`（然后 `uv lock`，`uv.lock` 会自动更新）
- `CHANGELOG.md`

发布前：

```bat
python -m compileall -q main.py autostart.py core ui web service tests
ruff check .
powershell -ExecutionPolicy Bypass -File tests\run_all.ps1
build.bat
```

服务模式再做一次 Windows 手工验收：`--install`、标准用户进程/文件保护、任务管理器策略、备份恢复、`--uninstall`。

## 提交

- 一个提交只做一件事；说明写清楚影响范围。
- 不要在提交中包含 `.venv`、`dist`、`build`、`tests/last_run.log`、数据库或日志（`.gitignore` 已覆盖）。
- 上传前可运行 `git status --ignored` 确认没有误加文件。
