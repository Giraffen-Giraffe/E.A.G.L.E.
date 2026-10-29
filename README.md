# E.A.G.L.E.

**E**volutionary **A**utomated **G**enerative **L**earning **E**ngine
（進化型・自立・生成・学習エンジン）

注文書(PDF・FAX・スキャン画像・メール等)をドラッグ&ドロップするか監視フォルダに置くだけで、
同梱の画像言語モデル(VLM)が必要な情報を読み取り、アプリ内のデータベースへ登録します。
**読み取りはすべてお使いの PC(または自分で用意したサーバー)の中で行い、書類をクラウドへ送信しません。**

- 1台の PC で完結する **アプリ版(デスクトップ)** と、LAN 内のサーバーで複数人が共有する **Webアプリ版(サーバー)** の2通り
- 様式(レイアウト)にとらわれない読み取り。読み違いが多い発行元は、あとから **ROI(読取領域)** を足して精度を上げられる
- 人が直した内容を **学習** して、次回から自動で直す
- **xlsx / csv 出力**、**外部データベースへの書き出し**(PostgreSQL / MySQL / SQL Server / SQLite / Access 等)

---

## 必要な環境(最低スペック)

| | 最低(動作する) | 推奨: アプリ版 | 推奨: Webアプリ版サーバー |
|---|---|---|---|
| OS | Windows 10/11 64bit / Ubuntu 22.04 以降 / macOS 13 以降(Apple Silicon) | Windows 11 64bit | Windows 11 / Windows Server 2019 以降 / Ubuntu 22.04 以降 |
| CPU | 4コア(AVX2 対応) | 6コア以上 | 8コア以上 |
| メモリ | 16 GB | 16 GB 以上 | 32 GB 以上 |
| GPU | 不要(CPU で動作。1ページ数分かかる) | NVIDIA GeForce 等 **VRAM 10 GB 以上**(例: RTX 3060 12GB / 4070 / 5070) | NVIDIA **VRAM 12 GB 以上** |
| ストレージ | 空き 10 GB(SSD 推奨) | 空き 20 GB 以上の SSD | 空き 50 GB 以上の SSD(書類の保存量に応じて) |
| モデル | `light`(4B) | `standard`(8B) | `standard`(8B)または `high`(8B Q8) |
| 閲覧側 | — | — | Microsoft Edge / Google Chrome / Firefox の最新版 |

- NVIDIA GPU は CUDA 13 対応ドライバ(R580 以降)で CUDA 版を、それより古いドライバでは CUDA 12 版を自動で選びます。NVIDIA 以外の GPU(AMD / Intel)は Vulkan 版を使います。
- ソースから動かす場合は Python 3.10 以降(3.11〜3.14 推奨)、画面のビルドに Node.js 18 以降が必要です。配布用の exe を使う場合は不要です。

### モデルごとの目安

| プリセット | モデル | ダウンロード | VRAM / メモリの目安(コンテキスト 16k) |
|---|---|---|---|
| `tiny` | Qwen3-VL 2B (Q4_K_M) | 約 1.6 GB | 約 4 GB |
| `light` | Qwen3-VL 4B (Q4_K_M) | 約 3.0 GB | 約 6 GB |
| `standard`(既定) | Qwen3-VL 8B (Q4_K_M) | 約 5.8 GB | **約 8.2 GB(実測)** |
| `high` | Qwen3-VL 8B (Q8_0) | 約 9.9 GB | 約 13 GB |

処理時間の目安(測定環境: GeForce RTX 5070 Ti 16GB / `standard`): 1ページの注文書で 15〜25 秒、メール本文の注文で 4 秒前後。
VRAM が足りない場合は、設定画面で「GPU に載せる層数」を減らすか、コンテキスト長を 8192 に下げてください(CPU との併用になり遅くなります)。

---

## はじめかた

### インストーラで使う(おすすめ・Windows)

1. [Releases](https://github.com/saltea-Giraffe/E.A.G.L.E./releases) から `EAGLE-Setup-<版>.exe` をダウンロードして実行します。
   「Windows によって PC が保護されました」と表示された場合は「詳細情報」→「実行」を選んでください(コード署名をしていないため)。
2. スタートメニューの「E.A.G.L.E.」を起動します。
3. ホーム画面の「導入する」で読み取りエンジン(AI モデル)を導入します。初回だけ数 GB をダウンロードし、以降はインターネット不要です。
   GPU に合わせて推奨のモデルが選ばれます。

- 管理者権限は不要です(インストール時に「すべてのユーザー」を選んだ場合のみ必要)。
- 読み取りエンジンが使う Visual C++ ランタイムが入っていない PC では、インストール中に Microsoft から取得して導入します(管理者の確認画面が表示されます)。
- 書類・データベース・モデルは `%LOCALAPPDATA%\EAGLE` に保存されます。アンインストールしても残るため、不要な場合は手動で削除してください。
- **ポータブル版** `EAGLE-<版>-portable.zip`: 好きな場所に展開して `EAGLE.exe` を起動します。データは展開したフォルダの中に保存されます。
- **Webアプリ版**: スタートメニューの「E.A.G.L.E. サーバー(Webアプリ版)」を起動し、初回に管理者アカウントを作成します。
  ほかの PC からは `http://<サーバーにした PC の IP アドレス>:18770/` で利用できます。

### ソースから使う(開発者向け)

アプリ版(1台の PC で使う):

```powershell
git clone https://github.com/saltea-Giraffe/E.A.G.L.E..git
cd E.A.G.L.E.
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1      # 依存の導入・画面のビルド・VLM の導入(初回のみ数 GB)
start_desktop.bat                                                # 起動(専用ウィンドウで開きます)
```

- 低スペックの PC は `scripts\setup.ps1 -Preset light` を使ってください。

Webアプリ版(LAN 内のサーバーで複数人が使う):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Server
.venv\Scripts\python -m eagle user add admin --role admin       # 管理者を作成
start_server.bat                                                 # http://<サーバーのIP>:18770/ で利用
```

- 利用者ごとにログインします(権限: 管理者 / 編集者 / 閲覧者)。
- Windows 起動時に自動で立ち上げる: `scripts\register_server_task.ps1`(管理者として実行)
- HTTPS で公開する場合は `config\eagle.example.toml` を `eagle.toml` にコピーして `ssl_certfile` / `ssl_keyfile` を設定するか、リバースプロキシを使ってください。
- 複数台から同じ DB を使う、または DB を別サーバーに置く場合は `storage.database_url` に PostgreSQL 等を指定できます。
- 推論を LAN 内の別の GPU サーバー(vLLM / llama-server / Ollama)に任せることもできます(設定 → VLM)。

### 動作確認用のサンプル

```powershell
.venv\Scripts\python scripts\make_samples.py samples   # 架空の注文書(電子PDF・スキャン画像・FAX PDF)とマスタの例を作成
.venv\Scripts\python -m eagle process samples\sample_order_fax.pdf   # 画面なしで読み取り結果を表示
```

---

## 使いかた

1. **取り込む** — 画面のどこかに注文書をドラッグ&ドロップ。または「監視モード」を ON にして、設定した監視フォルダに置く(取込後は `_eagle_done` フォルダへ移動)。
   対応形式: PDF(電子 / スキャン / FAX)、PNG・JPEG・TIFF(複数ページ)・BMP・WebP、メール(.eml。添付の PDF・画像と本文)、Excel・CSV・テキスト
2. **確認する** — 「注文書」一覧の各書類を開くと、左に原本、右に読み取り結果(発注元・納品先・注文番号・日付・明細)。
   要確認の項目は理由付きで表示されます。修正して「確定」します。
3. **出力・連携する** — 一覧から Excel(.xlsx)/ CSV へ出力。外部 DB 連携を設定すれば、確定時などに自動で書き出せます。

### 読み取る項目

- 書類: 書類名・注文番号・注文日・希望納期・客先注番・小計・消費税・合計・支払条件・備考
- 発注元: 会社名・部署/支店・担当者・住所・TEL・FAX(顧客マスタがあれば顧客コードに照合)
- 納品先: 会社名・部署・担当者・住所・TEL・エンドユーザー(顧客マスタがあれば顧客コードに照合)
- 明細: 注文番号・商品コード・品名・数量・単位・単価・金額・納期・備考(商品マスタがあれば型番を確定)
- **追加項目**: 設定 → 追加項目 で、案件名・現場名など任意の項目を読み取り指示付きで追加できます(書類単位 / 明細行単位)

### 精度を上げる仕組み

| 仕組み | 内容 |
|---|---|
| 様式に依存しない読取 | 発注元・宛先・届け先を「役割」で判別。自社名(設定)を宛先として除外し、発注元との取り違えを防ぐ |
| **ROI(読取領域)** | テンプレートに「この欄にはこの項目がある」と領域を登録すると、その部分だけを切り出して精読する。電子 PDF なら文字情報を直接読む。スキャン位置のズレは印字範囲を基準に自動補正 |
| 読取ヒント | 発行元ごとの癖(「発注番号は品名の右横に行ごとにある」等)をテンプレートに書くと VLM へ伝わる |
| マスタ照合 | 商品マスタ: 区切り・全半角・O/0・I/1 の誤読、接頭辞、略記、単価の違いまで吸収して型番を確定。顧客マスタ: 担当者・部署・TEL・〒・住所を根拠に、同じ会社の別レコード(担当者違い・支店違い)まで選び分ける |
| 検証と後処理 | 税・合計行の除去、控えページの重複除去、金額=単価×数量 の検算、電子 PDF の文字情報による注文番号の検証、日付(和暦・年なし)の正規化 |
| 学習 | 確定時の修正(値の置換・型番の読み替え・空欄への既定値・顧客の利用実績)を記憶し、次回から自動で直す。修正の多い項目には ROI の追加を勧める |

詳しくは [docs/ROI_GUIDE.md](docs/ROI_GUIDE.md) を参照してください。

---

## セキュリティとデータの扱い

- 書類の画像・文字は、同梱の llama.cpp(127.0.0.1 で待ち受け)にだけ渡されます。外部への送信はありません(初回のモデル取得を除く)。
- 書類・DB・ログは `data/`(設定で変更可)に保存されます。リポジトリには含まれません(`.gitignore` 済み)。
- アプリ版は 127.0.0.1 でのみ待ち受け、Host ヘッダも検証します。Webアプリ版はログイン必須(PBKDF2 によるパスワード保存、総当たり対策、CSRF 対策)。

詳しくは [docs/SECURITY.md](docs/SECURITY.md) を参照してください。

---

## フォルダ構成

```
eagle/            本体(Python)
  pipeline/       読込・VLM 抽出・ROI・後処理・マスタ照合
  vlm/            VLM クライアント・llama-server の管理・導入
  services/       処理キュー・監視フォルダ・出力・外部DB・認証
  api/            Web API(FastAPI)
frontend/         画面(React + TypeScript)。ビルド結果は eagle/web/static/ へ
scripts/          導入・ビルド・サービス登録・サンプル作成
packaging/        配布用 exe とインストーラの定義(PyInstaller / Inno Setup)
config/           起動設定の例
docs/             設計・ROI・セキュリティ・運用の資料
tests/            自動テスト
bin/ models/ data/  (実行時に作られる。Git 管理外)
```

## 開発

```powershell
.venv\Scripts\python -m pip install -e ".[desktop,dev]"
.venv\Scripts\python -m pytest              # テスト(VLM は差し替えて実行)
.venv\Scripts\python -m eagle desktop --browser   # API + 画面(ブラウザで開く)
cd frontend; npm run dev                     # 画面の開発サーバー(/api は 18760 へ中継)
```

配布物を作る(Inno Setup 6 が必要):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
# → dist\EAGLE-Setup-<版>.exe(インストーラ)と dist\EAGLE-<版>-portable.zip(ポータブル版)
```

インターネットに繋がらない PC 向けには `scripts\build_desktop.ps1 -IncludeVlm -Zip` で llama.cpp とモデルも同梱できます(数 GB。再配布時の注意は [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md))。

## ライセンス

E.A.G.L.E. は [Apache License 2.0](LICENSE) のもとで配布するフリーソフトです(Copyright 2026 saltea-Giraffe)。
無料で利用・改変・再配布できます。再配布する場合は [LICENSE](LICENSE) と [NOTICE](NOTICE) を同梱してください。

## 同梱・利用するソフトウェアとモデル

- [llama.cpp](https://github.com/ggml-org/llama.cpp)(MIT License)— 初回導入時に公式の配布物を取得
- [Qwen3-VL](https://huggingface.co/Qwen)(Apache License 2.0)— 初回導入時に公式の GGUF を取得
- [PDFium](https://pdfium.googlesource.com/pdfium/) / [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)(Apache-2.0 / BSD-3-Clause)— PDF の描画と文字情報の取得
- FastAPI / SQLAlchemy / OpenCV / Pillow / openpyxl / watchdog / pywebview / React ほか

いずれも無料での再配布ができるライセンスです。一覧と注意点は [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。
配布用の exe には、各ライブラリのライセンス文書を `licenses/` フォルダとして同梱します(`scripts\build_desktop.ps1` が自動で集めます)。
