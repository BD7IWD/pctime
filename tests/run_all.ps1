# 单机版客户端回归测试：清干净 → 启动 → 依次跑测试
# 用法：powershell -ExecutionPolicy Bypass -File tests\run_all.ps1
# 作者：野怼怼；https://www.bd7iwd.com
# 许可：MIT
$ErrorActionPreference = 'Continue'
$R = Split-Path -Parent $PSScriptRoot
$DATA = Join-Path $env:LOCALAPPDATA 'PCTimeControlLite'
$PORT = 8080

function Kill-All {
    # watchdog 会在 3 秒内把主程序拉起来，所以要多轮杀。
    # 按命令行里是否包含本项目的 main.py 来筛选，避免误杀其他 Python 进程，
    # 也不依赖具体的 Python 版本目录名。
    for ($i = 0; $i -lt 5; $i++) {
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                ($_.Name -match '^(python|pythonw|PCTimeControlLite)') -and
                (($_.CommandLine -like "*$R*main.py*") -or ($_.Name -eq 'PCTimeControlLite.exe'))
            } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 900
    }
}

Write-Host '=== 1. 停掉所有实例（含 watchdog）===' -ForegroundColor Cyan
Kill-All

Write-Host '=== 2. 等端口释放 ===' -ForegroundColor Cyan
$released = $false
for ($i = 0; $i -lt 30; $i++) {
    $c = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue
    if (-not $c) { $released = $true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $released) { Write-Host "❌ 端口 $PORT 仍被占用，测试中止" -ForegroundColor Red; exit 1 }
Write-Host "  端口 $PORT 已释放" -ForegroundColor Green

Write-Host '=== 3. 清空数据（回到首次使用状态）===' -ForegroundColor Cyan
Remove-Item $DATA -Recurse -Force -ErrorAction SilentlyContinue
# 用量同时写在注册表里，不清掉会让“当天已用时间”从旧值开始
Remove-Item -Path 'HKCU:\Software\PCTimeControlLite' -Recurse -Force -ErrorAction SilentlyContinue

Write-Host '=== 4. 启动客户端 ===' -ForegroundColor Cyan
# 测试环境：不真正静音；跳过本地首次设置窗口，改由网页 /setup 完成
$env:PCTL_DISABLE_AUDIO = '1'
$env:PCTL_SKIP_LOCAL_SETUP = '1'
$env:PCTL_SKIP_AUTOSTART = '1'
$mainPy = Join-Path $R 'main.py'
# 用绝对路径启动，Kill-All 才能按命令行准确识别本项目进程
Start-Process -FilePath 'python' -ArgumentList ('"{0}"' -f $mainPy) -WorkingDirectory $R -WindowStyle Hidden
# 子进程已继承环境变量；清掉当前 PowerShell 会话里的测试开关
Remove-Item Env:PCTL_DISABLE_AUDIO -ErrorAction SilentlyContinue
Remove-Item Env:PCTL_SKIP_LOCAL_SETUP -ErrorAction SilentlyContinue
Remove-Item Env:PCTL_SKIP_AUTOSTART -ErrorAction SilentlyContinue

# 等它真正起来（日志里出现 已启动 且端口在听）
$up = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    $log = Join-Path $DATA 'client.log'
    if (Test-Path $log) {
        $t = Get-Content $log -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($t -match "已启动 http://0\.0\.0\.0:$PORT") {
            $c = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue
            if ($c) { $up = $true; break }
        }
    }
}
if (-not $up) {
    Write-Host "❌ 客户端未能在 $PORT 上启动，日志：" -ForegroundColor Red
    Get-Content (Join-Path $DATA 'client.log') -Encoding UTF8 -ErrorAction SilentlyContinue | Select-Object -Last 20
    exit 1
}
Write-Host "  已就绪 http://127.0.0.1:$PORT" -ForegroundColor Green

$tests = @('_test_web_lite.py', '_test_e2e_lite.py', '_test_units_lite.py',
           '_test_backdoor.py', '_test_ui_lite.py', '_test_service_units.py')
$fail = 0
foreach ($t in $tests) {
    Write-Host "`n########## $t ##########" -ForegroundColor Cyan
    python (Join-Path $R "tests\$t")
    if ($LASTEXITCODE -ne 0) { $fail++ }
}

Write-Host "`n=== 清理测试环境（停止客户端、保存日志、删除测试密码和数据）===" -ForegroundColor Cyan
Kill-All
$lastLog = Join-Path $R 'tests\last_run.log'
if (Test-Path (Join-Path $DATA 'client.log')) {
    Copy-Item (Join-Path $DATA 'client.log') $lastLog -Force -ErrorAction SilentlyContinue
}
Remove-Item $DATA -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path 'HKCU:\Software\PCTimeControlLite' -Recurse -Force -ErrorAction SilentlyContinue
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
    -Name 'PCTimeControlLite' -ErrorAction SilentlyContinue
Write-Host "  测试日志已保存到 $lastLog" -ForegroundColor Green

Write-Host "`n========== 失败脚本数: $fail / $($tests.Count) ==========" -ForegroundColor $(if ($fail -eq 0) { 'Green' } else { 'Red' })
exit $fail
