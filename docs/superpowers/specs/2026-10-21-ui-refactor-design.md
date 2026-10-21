# PC_UES_TIME_CONTROL_LITE 界面重构与功能增强设计

日期：2026-10-21
状态：已实施（2.2.0）
目标版本：2.2.0

## 1. 背景与目标

现有版本使用 PyQt5，PyInstaller 单文件产物约 38 MB，日常只需托盘、锁屏、提醒和本地控制面板，Qt 的体量不划算。本次重构在保持现有 HTML 手机管理页、SQLite 存储、键盘钩子和 watchdog 行为不变的前提下：

1. 移除 PyQt5，改用 Python 标准库 Tkinter + Win32 ctypes，降低产物体积；
2. 支持自定义全局快捷键唤出本地控制面板；
3. 支持在手机网页修改 HTTP 监听端口；
4. 首次运行本地强制设置管理密码（同时设置解锁密码）；
5. 支持隐藏系统托盘图标；
6. 锁屏期间静音并完整模拟死机蓝屏状态（冻结输入、隐藏光标、进度到 100%）。

## 2. 非目标

- 不引入第三方 GUI/音频依赖；
- 不重写 Web 管理页的视觉风格；
- 不改变用量双写（SQLite + 注册表）、密码算法、锁定后门、键盘钩子与 watchdog 的核心策略；
- 不支持 Linux/macOS 运行完整程序（Win32 API 依赖不变）。

## 3. 技术选型

| 用途 | 方案 |
|---|---|
| 本地窗口 | Tkinter（stdlib）：锁屏、假弹窗、提醒、控制面板、首次设置 |
| 系统托盘 | ctypes `Shell_NotifyIconW` + 隐藏消息窗口 + 独立消息线程 |
| 全局热键 | ctypes `RegisterHotKey` + 独立消息线程 |
| 单实例 | ctypes `CreateMutexW` 命名互斥体 |
| 多显示器 | ctypes `EnumDisplayMonitors` / `GetMonitorInfoW` |
| 静音 | ctypes COM 调用 Core Audio `IAudioEndpointVolume` |
| 主循环 | 隐藏 `tk.Tk()` + `root.after`；后台线程通过 `queue.Queue` 回传事件 |

预计单文件产物 10–15 MB，最终以 Windows 实测为准。

## 4. 模块结构

```
main.py                  Tk 主循环、App 状态机、watchdog、单实例
core/
  config.py              路径、版本、端口校验
  store.py               SQLite + 注册表双写；settings 增加 hotkey/tray_hidden
  hotkey.py       [新]   热键字符串解析/规范化（纯逻辑，可跨平台测试）
  backdoor.py     [新]   连点计数（纯逻辑，可跨平台测试）
  audio.py        [新]   Core Audio 静音/恢复 + 异常退出恢复
  guard.py               键盘钩子：锁屏时吞掉全部按键，密码框期间挂起
  netinfo.py             局域网 IP（不变）
ui/
  win32.py        [新]   托盘、热键、互斥体、多屏枚举、DPI、Win32 常量
  lock_screen.py         Tkinter 蓝屏/假弹窗/密码框 + 进度模型
  toast.py               Tkinter 提醒横幅
  panel.py        [新]   本地控制面板 + 首次运行设置窗口
web/
  server.py              新增 /local、/port；WebService 支持严格端口重启
  pages.py               设置页新增本机设置、端口修改
  static/app.css         复用现有样式，补充复选框/端口表单
autostart.py             不变
build.bat                去掉 PyQt5 隐藏导入，排除 Qt 模块
```

## 5. 功能规格

### 5.1 移除 PyQt5

- 删除全部 `PyQt5` 导入与 `make_icon` 等 Qt 绘制代码。
- 托盘图标直接加载 `app.ico`（`LoadImageW`），加载失败退回系统默认图标。
- 锁屏/提醒/面板使用 Tkinter。
- 打包参数移除 `--hidden-import PyQt5.sip`，增加排除 PyQt5/PyQt6/PySide 的模块。
- 运行时不依赖任何第三方 Python 包；Tkinter/Tcl 随 Python 标准安装提供。

### 5.2 自定义唤出快捷键

- 默认 `Ctrl+Alt+M`。
- 支持修饰键 `Ctrl` / `Alt` / `Shift` / `Win`（大小写不敏感）和按键 `A–Z`、`0–9`、`F1–F12`。
- 至少包含 `Ctrl`、`Alt`、`Win` 之一；不允许重复修饰键。
- 可在网页「设置 → 本机设置」和本地控制面板修改。
- `RegisterHotKey` 注册失败（冲突/非法）时保留旧热键，写事件日志；如果托盘同时被隐藏，自动强制显示托盘。
- 锁屏期间热键不生效，防止孩子唤出控制面板。

### 5.3 修改监听端口

- 网页设置页与本地控制面板均可修改，范围 1024–65535。
- 拒绝浏览器不安全端口（如 6666）和已被占用的端口。
- 网页提交后由主线程停止旧 HTTP 服务、按严格端口启动新服务，并写回 `config.json`。
- 启动失败自动回滚旧端口；网页确认页同时显示新旧地址。
- `--port` 命令行和 `config.json` 仍可作为启动时的初始端口；启动时若端口被占用，沿用现有自动挑选逻辑。

### 5.4 首次运行强制设置

- 启动时若 `admin_hash` 或 `unlock_hash` 为空，显示本地模态设置窗口。
- 必须设置：管理密码（≥6 位）、解锁密码（≥4 位）、每日额度（1–1440 分钟）。
- 完成前不启动 Web 服务、托盘、热键和计时器；窗口关闭按钮无效。
- 提供「退出程序」，但下次启动仍会强制设置；开机自启只在设置完成后写入。
- 网页 `/setup` 保留，用于历史数据缺密码等边缘情况。

### 5.5 隐藏托盘图标

- 设置项 `tray_hidden`（默认 0）；网页与本地控制面板均可切换。
- 隐藏后托盘图标从通知区域移除，控制面板仍可通过快捷键唤出。
- 启动时若托盘隐藏且热键注册失败，自动显示托盘并写回 `tray_hidden=0`。
- 托盘隐藏时，启动后用提醒横幅告知当前快捷键和访问地址。

### 5.6 锁屏死机模拟

- **静音/防休眠**：锁屏时读取默认播放设备的静音状态并保存，然后静音，同时阻止系统休眠和关闭显示器；解锁、退出时恢复。状态持久化到数据目录；若进程被强杀，下次启动自动恢复遗留静音。COM 失败只记日志。
- **冻结输入**：锁屏期间键盘钩子吞掉所有按键；弹出解锁密码框时切换为密码输入模式（只放行普通字符，仍拦截 Win/Ctrl/Alt 组合键）；锁屏窗口使用 `cursor="none"` 隐藏鼠标指针，全屏置顶覆盖所有显示器。
- **完整进度**：蓝屏进度从 0% 按随机步进走到 100%（约 25–45 秒），到达 100% 后保持画面不重启。
- 保留 6 秒内连点 10 次弹出解锁密码框的后门。
- 测试通过 `PCTL_DISABLE_AUDIO=1` 禁止真实静音。

## 6. 数据与配置

### 6.1 settings 表新增列

```sql
ALTER TABLE settings ADD COLUMN hotkey TEXT NOT NULL DEFAULT 'Ctrl+Alt+M';
ALTER TABLE settings ADD COLUMN tray_hidden INTEGER NOT NULL DEFAULT 0;
```

`store.db()` 初始化时用 `PRAGMA table_info(settings)` 检测并自动迁移；`get_settings` 提供默认值；`update_settings` 白名单加入两列。

### 6.2 端口

端口继续存 `config.json`。网页修改端口时通过 `commands` 表推送 `set_port`，由主线程执行服务重启，避免 Web 线程直接操作服务实例。

### 6.3 音频状态

`core/audio.py` 在数据目录写 `audio.state`（JSON：`muted`、`prev_mute`），原子替换，避免与 `config.json` 的并发写冲突。

## 7. 事件与线程模型

- Tk 主线程：所有 Tk 操作、计时、状态机、端口重启、音频静音、密码校验。
- Web 线程：只读写 SQLite/配置读取，不导入 UI 模块。
- 托盘线程：隐藏消息窗口 + `Shell_NotifyIconW`，菜单操作推入 `queue.Queue`。
- 热键线程：`RegisterHotKey` + `GetMessage`，命中后推入同一队列。
- 主线程每 100 ms 轮询队列；每 1000 ms 执行 `tick()`。
- 密码框使用 Tk 嵌套事件循环（`wait_window`）期间的 tick 继续运行。

## 8. 错误处理

- 托盘启动失败：记录日志，热键与网页仍可用。
- 热键注册失败：保留旧值，写事件；托盘隐藏时强制显示托盘。
- 端口启动失败：回滚旧端口；记录事件。
- 音频 COM 失败：不静音、不阻塞锁定。
- Tk 窗口/画布异常：记录日志，锁屏状态仍由数据库和 tick 维持。

## 9. 测试

- `tests/_test_web_lite.py`：现有流程 + 本机设置校验（非法热键/端口被拒绝）。
- `tests/_test_e2e_lite.py`：现有时间耗尽锁屏/解锁流程。
- `tests/_test_units_lite.py` [新]：热键解析、端口校验、连点计数、音频状态机、数据库列迁移。
- `tests/_test_backdoor.py`：改为纯逻辑测试，不再依赖 Qt offscreen。
- `tests/_test_ui_lite.py` [新]：Tkinter 进度、连点绑定、首次设置、后门密码框、控制面板。
- `tests/run_all.ps1`：清空数据库/注册表用量，设置 `PCTL_DISABLE_AUDIO=1`、`PCTL_SKIP_LOCAL_SETUP=1`、`PCTL_SKIP_AUTOSTART=1`，更可靠地结束旧进程，依次运行六个脚本。
- Windows 手工验收：首次设置、热键唤出、隐藏托盘后热键恢复、网页改端口、锁屏静音/冻结/进度、手机解锁、后门解锁。

## 10. 验收标准

1. 构建产物不再包含 PyQt5，单文件体积明显下降（目标 < 15 MB）。
2. 首次启动未设置密码时无法进入待机、托盘、网页或计时。
3. 任意时刻按自定义热键可唤出控制面板；锁屏时热键无效。
4. 网页可改端口，成功后新地址可访问，失败自动回滚。
5. 托盘可隐藏/恢复；隐藏且热键失效时自动恢复托盘。
6. 锁屏时系统静音、光标隐藏、键盘冻结、进度到 100% 保持；解锁后声音恢复。
7. 现有回归测试全部通过。
