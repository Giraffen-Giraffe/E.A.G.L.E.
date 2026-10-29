<#
.SYNOPSIS
  E.A.G.L.E. の開発・実行環境を作る(Windows)。
.DESCRIPTION
  1. Python 仮想環境(.venv)を作り、依存パッケージを入れる
  2. 画面(frontend)をビルドする(Node.js が必要)
  3. 同梱 VLM(llama.cpp + モデル)をダウンロードする(初回のみ。-SkipVlm で省略)
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Preset standard
#>
param(
    [ValidateSet("standard", "high", "light", "tiny")] [string]$Preset = "standard",
    [string]$Variant = "auto",
    [switch]$SkipVlm,
    [switch]$Server
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = $null
foreach ($v in @("3.13", "3.12", "3.11", "3.14", "3.10")) {
    try { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = @("py", "-$v"); break } } catch {}
}
if (-not $py) { $py = @("python") }
Write-Host "== Python: $($py -join ' ')"
if (-not (Test-Path ".venv")) { & $py[0] $py[1..($py.Length - 1)] -m venv .venv }
$vpy = Join-Path $root ".venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip
$extras = if ($Server) { "" } else { "[desktop]" }
& $vpy -m pip install -e ".$extras"

if (Get-Command npm -ErrorAction SilentlyContinue) {
    Write-Host "== 画面をビルド"
    Push-Location frontend
    npm ci; if ($LASTEXITCODE -ne 0) { npm install }
    npm run build
    Pop-Location
} else {
    Write-Warning "Node.js(npm) が見つかりません。画面のビルドを省略しました(https://nodejs.org/ から導入後に frontend で npm run build)"
}

if (-not $SkipVlm) {
    Write-Host "== 同梱 VLM を導入(初回のみ数 GB をダウンロード)"
    & $vpy -m eagle setup-vlm --preset $Preset --variant $Variant
}
Write-Host ""
Write-Host "完了: start_desktop.bat(アプリ版) / start_server.bat(Webアプリ版) で起動できます。"
