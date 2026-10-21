# PC_UES_TIME_CONTROL_LITE 服务化强化保护设计

日期：2026-10-21
状态：已实施（2.3.0）
目标版本：2.3.0

## 1. 目标

在现有便携单 exe 的基础上，为**孩子使用标准 Windows 用户**的家庭提供可选的强化保护：

1. 标准用户无法结束保护进程；
2. 标准用户无法删除/修改程序和设置数据；
3. 数据库/设置丢失或损坏时自动恢复；
4. 可选对非管理员会话禁用任务管理器；
5. 保留免安装便携模式。

## 2. 明确的边界

- **不实现真正的进程隐藏**。用户态无法从任务管理器隐藏进程；内核驱动/EPROCESS 断链属于 rootkit 技术，不稳定且容易被杀软判定为恶意软件，本项目不采用。
- 标准用户在任务管理器/服务列表中仍能看到进程，但**无法结束** SYSTEM/LocalService 进程。
- 管理员账户、安全模式、物理接触（PE/另一块硬盘）仍可绕过；任何用户态方案都无法对抗管理员。
- 任务管理器策略是用户策略，孩子可能尝试修改；守护服务周期恢复。

## 3. 进程与权限

| 组件 | 身份 | 职责 |
|---|---|---|
| `PCTimeGuardSvc` | Windows 服务 / LocalSystem | ACL 修复、防删除句柄、备份/恢复、会话监控、启动 SYSTEM 代理、任务管理器策略、看护 Web 服务 |
| `PCTimeWebSvc` | Windows 服务 / LocalService | 状态机 + HTTP 服务；不接触 UI，不以 SYSTEM 暴露网络 |
| `PCTimeAgent` | SYSTEM，运行在非管理员用户会话 | 托盘、热键、本地面板、锁屏、静音、键盘钩子、keep-awake；轮询 DB 执行锁定 |
| 安装器 | 管理员提权 | 复制程序、建 ACL、建服务、防火墙、迁移数据、卸载 |

家长的管理员会话不启动代理、不锁屏；孩子/共用标准账户会话受保护。

## 4. 文件与数据

- `C:\Program Files\PCTimeControlLite\`：SYSTEM、Administrators 完全控制，LocalService 只读执行，普通用户只读执行（不能写/删；便于标准账户触发 UAC 运行卸载器）。
- `C:\ProgramData\PCTimeControlLite\`：SYSTEM、Administrators、LocalService 完全控制；普通用户无权限。
- 守护服务持有 exe、数据目录、`data.db` 的防删除句柄（共享读写、不共享 delete）。
- 每 10 秒备份数据库到受保护目录，并导出不含密码哈希的 JSON 到 HKLM；数据库缺失/损坏时启动恢复；JSON 可重建设置/用量（密码需重设）。
- 每 30 秒重新应用 ACL；SCM 失败重启 + 守护服务看护。

## 5. 代理启动

守护服务枚举活动会话（WTS），跳过管理员会话，使用自身 SYSTEM 令牌复制并设置 `TokenSessionId`，通过 `CreateProcessAsUserW` 在 `winsta0\default` 启动 `--agent --session N`。代理写心跳文件，守护服务在心跳过期时结束并重启代理；同一会话用 `Local\PCTimeAgent_<session>` 互斥。

## 6. 状态机拆分

- Web 核心（LocalService）负责计时、用量落盘、额度锁定：写 `locked=1`，并把 `toast` 留给代理；端口修改在自己的进程内重启 HTTP 服务。
- 代理每 500ms 读 DB：`locked` 变化时进入/退出锁屏（静音/键盘冻结/keep-awake），处理 `toast` 指令；本地面板的所有操作只写 DB，由 Web 核心的 tick 继续计时。
- 端口修改由面板写入 `set_port` 指令，Web 核心消费并重启自己的 HTTP 服务；代理只负责显示/发起。

## 7. 任务管理器策略

- `settings.disable_taskmgr` 默认 1。
- 守护服务对每个非管理员活动会话写 `HKU\<SID>\Software\Microsoft\Windows\CurrentVersion\Policies\System\DisableTaskMgr=1`，每轮（约 1 秒）重写；关闭开关后写 0。
- 网页「设置 → 本机设置」提供服务模式开关；便携模式不显示。

## 8. 安装/卸载

- `--install`（UAC）：停止旧实例 → 复制到 Program Files → 迁移所有用户 profile 的便携数据 → ACL → **管理员现场设置管理/解锁密码（若尚未初始化）** → HKLM → 用 SCM API 创建 `PCTimeGuard`/`PCTimeWeb`（Web 依赖 Guard、失败重启）→ 防火墙 → 启动。这样孩子无法在首次登录时抢先设置密码。
- `--uninstall`（UAC + 管理/解锁密码，`--force` 可绕过）：停止/删除服务、结束代理、清任务管理器策略/防火墙/ACL/注册表/数据；运行中的已安装 exe 计划重启后删除。
- 便携模式保留；已安装 exe 双击只提示服务正在运行。

## 9. 安全加固补充

- 本地面板打开需管理密码，3 分钟自动隐藏；连续失败限流。
- Web 登录失败限流。
- 安装器在创建服务前完成首次设置，避免孩子抢先设置。

## 10. 测试

- `tests/_test_service_units.py`：数据目录覆盖、备份/恢复、JSON 重建、ACL 参数、服务 binPath、策略路径；可跨平台。
- Linux + xvfb 开发验证：`main.py --web --data-dir <tmp>` + `main.py --agent --session 0 --data-dir <tmp>`，验证首次设置由 Web 完成后代理启动、锁定/解锁、心跳。
- Windows 手工验收：SCM 服务安装/重启、SYSTEM 代理会话、ACL 拒绝标准用户、任务管理器策略、备份恢复、卸载。
