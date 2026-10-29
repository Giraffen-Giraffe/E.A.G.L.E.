# 第三者のソフトウェアとモデル

E.A.G.L.E. は次のソフトウェア・モデルを利用しています。いずれも無料での再配布ができるライセンスです。
配布用の exe(`scripts\build_desktop.ps1` で作成)には、各ライセンス文書を `licenses/` フォルダとして同梱します。

## 読み取りエンジン(初回導入時にダウンロード)

| 名前 | ライセンス | 備考 |
|---|---|---|
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | MIT | 公式の配布物をそのまま使用 |
| [Qwen3-VL](https://huggingface.co/Qwen)(GGUF) | Apache-2.0 | Qwen チーム(Alibaba Cloud)の公開モデル |
| NVIDIA CUDA ランタイム(cudart / cuBLAS) | NVIDIA CUDA EULA | llama.cpp の CUDA 版に含まれる。NVIDIA GPU 環境のみ |

`-IncludeVlm` で llama.cpp やモデルを exe と一緒に配る場合は、上記のライセンスに従ってください。
特に CUDA ランタイムは NVIDIA の使用許諾(再配布可能ファイルの条件)が適用されます。
一般に配布する場合は同梱せず、利用者の PC で導入してもらう形(既定の動作)をおすすめします。

## 本体が使う Python パッケージ

| 名前 | ライセンス |
|---|---|
| Python | PSF-2.0 |
| FastAPI / Starlette / Pydantic / SQLAlchemy / AnyIO / h11 / annotated-types / annotated-doc / typing-inspection | MIT / BSD-3-Clause |
| Uvicorn / httptools / websockets / watchfiles / python-dotenv / PyYAML / click | BSD-3-Clause / MIT |
| python-multipart | Apache-2.0 |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) / [PDFium](https://pdfium.googlesource.com/pdfium/) | Apache-2.0 / BSD-3-Clause(PDFium が内部で使うライブラリは下記) |
| Pillow | MIT-CMU |
| NumPy | BSD-3-Clause ほか |
| OpenCV(opencv-python-headless) | Apache-2.0(同梱ライブラリは LICENSE-3RD-PARTY.txt) |
| openpyxl / et_xmlfile | MIT |
| watchdog | Apache-2.0 |
| httpx / httpcore / idna | BSD-3-Clause |
| certifi | MPL-2.0 |
| opentelemetry-api | Apache-2.0 |
| typing_extensions | PSF-2.0 |
| pywebview / bottle / proxy_tools | BSD-3-Clause / MIT |
| pythonnet / clr_loader / cffi / pycparser | MIT / MIT-0 / BSD-3-Clause |

## 画面(JavaScript)

| 名前 | ライセンス |
|---|---|
| React / React DOM / scheduler | MIT |

開発時だけ使うもの(TypeScript・Vite・pytest・reportlab・PyInstaller)は配布物に含まれません。

## 任意の外部 DB ドライバ(利用者が必要に応じて導入)

| 名前 | ライセンス |
|---|---|
| psycopg(PostgreSQL) | LGPL-3.0(改変せずに利用する限り本体のライセンスに影響しない) |
| PyMySQL | MIT |
| pyodbc / sqlalchemy-access | MIT |

## 謝辞(ライセンスが表示を求めているもの)

- Portions of this software are copyright © The FreeType Project (www.freetype.org). All rights reserved.
- This software is based in part on the work of the Independent JPEG Group.
- PDFium が内部で使う Abseil / AGG / fast_float / ICU / Little CMS / libjpeg-turbo / OpenJPEG / libpng / zlib 等のライセンス文書は、
  配布物の `licenses/python/pypdfium2/` にあります。
