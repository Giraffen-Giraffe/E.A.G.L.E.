<#
.SYNOPSIS
  Webアプリ版を Windows 起動時に自動で立ち上げる(タスク スケジューラに登録)。管理者として実行。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\register_server_task.ps1
  powershell -ExecutionPolicy Bypass -File scripts\register_server_task.ps1 -Exe "C:\EAGLE\EAGLE-Server.exe"
  powershell -ExecutionPolicy Bypass -File scripts\register_server_task.ps1 -Unregister
#>
param([string]$Exe = "", [string]$TaskName = "EAGLE-Server", [switch]$Unregister)
$ErrorActionPreference = "Stop"
if ($Unregister) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; Write-Host "登録を解除しました"; return }
$root = Split-Path -Parent $PSScriptRoot
if ($Exe) {
    $action = New-ScheduledTaskAction -Execute $Exe -Argument "server" -WorkingDirectory (Split-Path $Exe)
} else {
    $py = Join-Path $root ".venv\Scripts\python.exe"
    $action = New-ScheduledTaskAction -Execute $py -Argument "-m eagle server" -WorkingDirectory $root
}
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "登録しました: $TaskName(次回の Windows 起動時から自動起動。今すぐ起動: Start-ScheduledTask $TaskName)"
Write-Host "※ 管理者アカウントは先に作成してください: python -m eagle user add admin --role admin"
