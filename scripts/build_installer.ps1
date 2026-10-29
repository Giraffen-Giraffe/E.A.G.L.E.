<#
.SYNOPSIS
  配布物を作る: インストーラ(dist\EAGLE-Setup-<版>.exe)とポータブル版(dist\EAGLE-<版>-portable.zip)。
.DESCRIPTION
  1. scripts\build_desktop.ps1 で exe 一式(dist\EAGLE\)を作る
  2. Inno Setup 6 でインストーラを作る
  3. exe 一式に portable.txt を足して zip にする(データを exe の隣に保存する版)
  読み取りエンジン(llama.cpp・モデル)は含めない。初回起動時にアプリが導入を案内する。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
#>
param([string]$Iscc = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$vpy = Join-Path $root ".venv\Scripts\python.exe"
$version = (& $vpy -c "import eagle; print(eagle.__version__)").Trim()

if (-not $Iscc) {
    $Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Iscc) { throw "Inno Setup 6 が見つかりません(https://jrsoftware.org/isinfo.php から導入してください)" }

& (Join-Path $PSScriptRoot "build_desktop.ps1")
$out = Join-Path $root "dist\EAGLE"
if (Test-Path (Join-Path $out "bin")) { throw "dist\EAGLE に読み取りエンジンが含まれています。-IncludeVlm なしで作り直してください" }

Write-Host "== インストーラを作成 ($version)"
& $Iscc "/DAppVersion=$version" "/Qp" (Join-Path $root "packaging\installer.iss")
if ($LASTEXITCODE -ne 0) { throw "インストーラの作成に失敗しました" }

Write-Host "== ポータブル版を作成"
& $vpy scripts\make_portable_zip.py $out (Join-Path $root "dist\EAGLE-$version-portable.zip")
if ($LASTEXITCODE -ne 0) { throw "ポータブル版の作成に失敗しました" }

Get-ChildItem (Join-Path $root "dist") -File | Where-Object { $_.Name -like "EAGLE-*" } |
    Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } } | Format-Table -AutoSize
