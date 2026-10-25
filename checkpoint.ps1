# 一键存档：把当前改动提交到本地 git（不联网、不影响运行）
# 用法：在本目录执行  powershell -ExecutionPolicy Bypass -File .\checkpoint.ps1 "改了什么"
# 不写说明也可以：powershell -ExecutionPolicy Bypass -File .\checkpoint.ps1

$git = 'C:\Users\Lenovo\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe'
if (-not (Test-Path $git)) { Write-Host "找不到 git：$git"; exit 1 }

Set-Location $PSScriptRoot
if (-not (Test-Path (Join-Path $PSScriptRoot '.git'))) {
    & $git init -q
    Write-Host "已初始化 git 仓库"
}

$msg = if ($args.Count -gt 0) { $args -join ' ' } else { "checkpoint " + (Get-Date -Format 'MM-dd HH:mm') }

& $git add -A
& $git -c user.name='zhilian' -c user.email='dev@local' commit -m $msg
Write-Host "---- 最近 3 次存档 ----"
& $git log --oneline -3
