<#
.SYNOPSIS
  配布用のアプリ(dist\EAGLE\EAGLE.exe / EAGLE-Server.exe)を作る。
.DESCRIPTION
  -IncludeVlm を付けると bin\llama と models\ も同梱し、インターネットに繋がらない PC へ
  フォルダごとコピーするだけで動く形にする(数 GB になる)。
#>
param([switch]$IncludeVlm, [switch]$Zip)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$vpy = Join-Path $root ".venv\Scripts\python.exe"
& $vpy -m pip install "pyinstaller>=6.16"
Push-Location frontend; npm run build; Pop-Location
& $vpy -m PyInstaller packaging\eagle.spec --noconfirm --distpath dist --workpath build
$out = Join-Path $root "dist\EAGLE"
Copy-Item config\eagle.example.toml $out
Copy-Item README.md, LICENSE, NOTICE, THIRD_PARTY_NOTICES.md $out
if ($IncludeVlm) {
    foreach ($d in @("bin\llama", "models")) {
        if (Test-Path $d) { Copy-Item $d (Join-Path $out $d) -Recurse -Force }
        else { Write-Warning "$d がありません(先に python -m eagle setup-vlm を実行)" }
    }
}
# 同梱するライブラリ・モデルのライセンス文書を licenses\ に集める
& $vpy scripts\collect_licenses.py $out
if ($Zip) { Compress-Archive -Path $out -DestinationPath (Join-Path $root "dist\EAGLE.zip") -Force }
Write-Host "完了: $out"
