import { useEffect, useState } from "react";
import { api } from "../api";
import { Card, Empty, Field, KeyValueInput, LinesInput, Modal, Progress, Tabs, Toggle, fmtBytes, fmtDate, navigate, useAction, useInterval } from "../components/ui";
import type { FieldsPayload, Info, Settings } from "../types";

type Tab = "vlm" | "extraction" | "watch" | "fields" | "integrations" | "users" | "logs";

export default function SettingsPage({ info, tab }: { info: Info; tab?: string }) {
  const [st, setSt] = useState<Settings | null>(null);
  const { busy, run } = useAction();
  const isAdmin = info.user?.role === "admin";
  const current = (tab as Tab) || "vlm";

  const load = () => api.get<{ settings: Settings }>("/api/settings").then((r) => setSt(r.settings)).catch(() => undefined);
  useEffect(() => { load(); }, []);

  const save = (sections: string[]) =>
    st && run(() => api.put<{ settings: Settings }>("/api/settings", Object.fromEntries(sections.map((k) => [k, st[k]]))).then((r) => setSt(r.settings)), "設定を保存しました");
  const set = (section: string, patch: Settings) => setSt((s) => (s ? { ...s, [section]: { ...s[section], ...patch } } : s));

  const tabs: { key: Tab; label: string }[] = [
    { key: "vlm", label: "VLM(読取エンジン)" },
    { key: "extraction", label: "読取ルール" },
    { key: "watch", label: "監視フォルダ" },
    { key: "fields", label: "追加項目" },
    { key: "integrations", label: "外部DB連携" },
    ...(info.auth_required ? [{ key: "users" as Tab, label: "利用者" }] : []),
    { key: "logs", label: "ログ" },
  ];

  return (
    <div className="page">
      <header className="page-head"><h1>設定</h1></header>
      <Tabs<Tab> value={current} onChange={(k) => navigate(`#/settings/${k}`)} tabs={tabs} />
      {!isAdmin && current !== "logs" && <div className="notice">設定の変更は管理者のみ可能です。</div>}
      {st && current === "vlm" && <VlmTab st={st} set={set} save={() => save(["vlm", "render"])} busy={busy} isAdmin={isAdmin} />}
      {st && current === "extraction" && <ExtractionTab st={st} set={set} save={() => save(["extraction", "matching", "learning", "export"])} busy={busy} />}
      {st && current === "watch" && <WatchTab st={st} set={set} save={() => save(["watch"])} busy={busy} />}
      {current === "fields" && <FieldsTab isAdmin={isAdmin} />}
      {current === "integrations" && <IntegrationsTab isAdmin={isAdmin} />}
      {current === "users" && <UsersTab info={info} />}
      {current === "logs" && <LogsTab />}
    </div>
  );
}

// ── VLM ───────────────────────────────────────────────────────────────
interface VlmStatus {
  backend: string;
  llama: { state: string; error: string; model: string; mmproj: string; binary_found: boolean; binary: string; models_dir: string; bin_dir: string; available: { models: { name: string; size: number }[]; mmproj: { name: string; size: number }[] }; log_tail: string[]; uptime_sec: number };
  setup: { state: string; step: string; file: string; percent: number; downloaded: number; total: number; error: string; log: string[] };
  presets: Record<string, { label: string; size_gb: number }>;
  accelerator: { nvidia: boolean; gpu: string; vram_mb: number; driver: string };
  recommended: string;
}

const LLAMA_STATE: Record<string, string> = {
  ready: "準備完了", starting: "起動中(モデル読込中)", stopped: "停止中", error: "エラー", external: "外部の llama-server に接続中",
};

function VlmTab({ st, set, save, busy, isAdmin }: { st: Settings; set: (s: string, p: Settings) => void; save: () => void; busy: boolean; isAdmin: boolean }) {
  const [vs, setVs] = useState<VlmStatus | null>(null);
  const [preset, setPreset] = useState("standard");
  const [test, setTest] = useState<string>("");
  const { run } = useAction();
  const load = () => api.get<VlmStatus>("/api/vlm/status").then(setVs).catch(() => undefined);
  useEffect(() => { load(); }, []);
  useInterval(load, vs?.setup.state === "running" || vs?.llama.state === "starting" ? 1500 : 6000);

  const v = st.vlm;
  const llama = v.llama;
  const setL = (p: Settings) => set("vlm", { llama: { ...llama, ...p } });
  const act = (a: string) => run(() => api.post(`/api/vlm/${a}`).then(load));
  const acc = vs?.accelerator;
  const recommended = vs?.recommended ?? "standard";

  return (
    <div className="grid-2 top">
      <Card title="読取エンジン">
        <Field label="推論先">
          <select value={v.backend} disabled={!isAdmin} onChange={(e) => set("vlm", { backend: e.target.value })}>
            <option value="llama_cpp">同梱の llama.cpp(ローカルで実行・推奨)</option>
            <option value="openai">別の推論サーバー(OpenAI 互換: vLLM / llama-server 等)</option>
            <option value="ollama">Ollama</option>
          </select>
        </Field>
        {v.backend === "llama_cpp" && (
          <>
            <div className="row gap wrap">
              <Field label="モデル(GGUF)">
                <select value={llama.model} disabled={!isAdmin} onChange={(e) => setL({ model: e.target.value })}>
                  <option value="">自動選択</option>
                  {vs?.llama.available.models.map((m) => <option key={m.name} value={m.name}>{m.name}({fmtBytes(m.size)})</option>)}
                </select>
              </Field>
              <Field label="画像エンコーダ(mmproj)">
                <select value={llama.mmproj} disabled={!isAdmin} onChange={(e) => setL({ mmproj: e.target.value })}>
                  <option value="">自動選択</option>
                  {vs?.llama.available.mmproj.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                </select>
              </Field>
            </div>
            <div className="row gap wrap">
              <Field label="コンテキスト長"><input type="number" value={llama.ctx} disabled={!isAdmin} onChange={(e) => setL({ ctx: Number(e.target.value) })} /></Field>
              <Field label="GPU に載せる層数" hint="999=全部 / 0=CPU のみ"><input type="number" value={llama.gpu_layers} disabled={!isAdmin} onChange={(e) => setL({ gpu_layers: Number(e.target.value) })} /></Field>
              <Field label="ポート"><input type="number" value={llama.port} disabled={!isAdmin} onChange={(e) => setL({ port: Number(e.target.value) })} /></Field>
            </div>
            <Field label="追加の起動引数(上級者向け)"><input value={llama.extra_args} disabled={!isAdmin} onChange={(e) => setL({ extra_args: e.target.value })} placeholder="例: --flash-attn on" /></Field>
            <Toggle checked={llama.autostart} disabled={!isAdmin} onChange={(x) => setL({ autostart: x })} label="アプリ起動時にモデルを読み込んでおく" />
          </>
        )}
        {v.backend === "openai" && (
          <>
            <Field label="URL" hint="例: http://192.168.x.x:8000/v1(LAN 内のサーバーを推奨)"><input value={v.openai.base_url} disabled={!isAdmin} onChange={(e) => set("vlm", { openai: { ...v.openai, base_url: e.target.value } })} /></Field>
            <Field label="モデル名"><input value={v.openai.model} disabled={!isAdmin} onChange={(e) => set("vlm", { openai: { ...v.openai, model: e.target.value } })} /></Field>
            <Field label="API キー(必要な場合)"><input type="password" value={v.openai.api_key} disabled={!isAdmin} onChange={(e) => set("vlm", { openai: { ...v.openai, api_key: e.target.value } })} /></Field>
          </>
        )}
        {v.backend === "ollama" && (
          <>
            <Field label="URL"><input value={v.ollama.base_url} disabled={!isAdmin} onChange={(e) => set("vlm", { ollama: { ...v.ollama, base_url: e.target.value } })} /></Field>
            <Field label="モデル名"><input value={v.ollama.model} disabled={!isAdmin} onChange={(e) => set("vlm", { ollama: { ...v.ollama, model: e.target.value } })} /></Field>
          </>
        )}
        <details className="advanced">
          <summary>詳細(出力形式・画像の解像度)</summary>
          <div className="row gap wrap">
            <Field label="JSON 出力の強制">
              <select value={v.json_mode} disabled={!isAdmin} onChange={(e) => set("vlm", { json_mode: e.target.value })}>
                <option value="schema">スキーマで強制(推奨)</option>
                <option value="object">JSON のみ</option>
                <option value="none">なし</option>
              </select>
            </Field>
            <Field label="最大出力トークン"><input type="number" value={v.max_tokens} disabled={!isAdmin} onChange={(e) => set("vlm", { max_tokens: Number(e.target.value) })} /></Field>
            <Field label="空返し時の再試行"><input type="number" value={v.retry_empty} disabled={!isAdmin} onChange={(e) => set("vlm", { retry_empty: Number(e.target.value) })} /></Field>
          </div>
          <div className="row gap wrap">
            <Field label="PDF 描画 DPI"><input type="number" value={st.render.dpi} disabled={!isAdmin} onChange={(e) => set("render", { dpi: Number(e.target.value) })} /></Field>
            <Field label="VLM に送る画像の長辺(px)" hint="大きいほど小さな文字に強いが遅い"><input type="number" value={st.render.max_long_side} disabled={!isAdmin} onChange={(e) => set("render", { max_long_side: Number(e.target.value) })} /></Field>
          </div>
          <div className="row gap wrap">
            <Field label="傾き補正">
              <select value={st.render.deskew} disabled={!isAdmin} onChange={(e) => set("render", { deskew: e.target.value })}>
                <option value="auto">スキャン画像のみ自動</option><option value="off">しない</option>
              </select>
            </Field>
            <Field label="画像の補正">
              <select value={st.render.enhance} disabled={!isAdmin} onChange={(e) => set("render", { enhance: e.target.value })}>
                <option value="off">しない</option><option value="contrast">コントラスト強調(薄い FAX 向け)</option>
              </select>
            </Field>
          </div>
        </details>
        {isAdmin && (
          <div className="row gap">
            <button className="btn primary" disabled={busy} onClick={save}>保存</button>
            <button className="btn" disabled={busy} onClick={async () => {
              setTest("テスト中…");
              try {
                const r = await api.post<{ ok: boolean; sec: number; model: string }>("/api/vlm/test");
                setTest(r.ok ? `応答 OK(${r.sec}秒 / ${r.model})` : "応答はありましたが内容が不正です");
              } catch (e) { setTest(`失敗: ${(e as Error).message}`); }
            }}>接続テスト</button>
            {test && <span className="muted">{test}</span>}
          </div>
        )}
      </Card>

      <div className="stack">
        {v.backend === "llama_cpp" && (
          <Card title="同梱 llama.cpp の状態" actions={isAdmin && (
            <div className="row gap">
              <button className="btn small" onClick={() => act("start")}>起動</button>
              <button className="btn small" onClick={() => act("restart")}>再起動</button>
              <button className="btn small ghost" onClick={() => act("stop")}>停止</button>
            </div>
          )}>
            <dl className="kv">
              <dt>状態</dt><dd><span className={`vlm-pill vlm-${vs?.llama.state}`}><span className="dot" />{LLAMA_STATE[vs?.llama.state ?? ""] ?? vs?.llama.state}</span></dd>
              <dt>モデル</dt><dd>{vs?.llama.model || "—"} {vs?.llama.mmproj && <span className="muted">+ {vs.llama.mmproj}</span>}</dd>
              <dt>実行ファイル</dt><dd className="small">{vs?.llama.binary_found ? vs.llama.binary : <span className="error-text">未導入({vs?.llama.bin_dir})</span>}</dd>
              <dt>GPU</dt><dd>{acc?.nvidia ? `${acc.gpu}(VRAM ${Math.round(acc.vram_mb / 1024)} GB)` : "NVIDIA GPU なし(CPU / Vulkan で動作)"}</dd>
            </dl>
            {vs?.llama.error && <div className="notice bad">{vs.llama.error}</div>}
            {vs?.llama.log_tail?.length ? <details><summary>ログ</summary><pre className="pre small">{vs.llama.log_tail.join("\n")}</pre></details> : null}
          </Card>
        )}
        {v.backend === "llama_cpp" && isAdmin && (
          <Card title="モデルの導入(初回のみ・インターネット接続が必要)">
            <p className="muted small">llama.cpp の実行ファイルと公開モデル(Qwen3-VL)をダウンロードします。導入後は完全にオフラインで動作し、書類が外部へ送られることはありません。</p>
            <div className="row gap wrap">
              <select value={preset} onChange={(e) => setPreset(e.target.value)}>
                {Object.entries(vs?.presets ?? {}).map(([k, p]) => (
                  <option key={k} value={k}>{p.label}(約 {p.size_gb} GB){k === recommended ? " ← お使いの PC に推奨" : ""}</option>
                ))}
              </select>
              <button className="btn primary" disabled={vs?.setup.state === "running"} onClick={() => run(() => api.post("/api/vlm/setup", { preset }).then(load))}>ダウンロードして導入</button>
            </div>
            {vs && vs.setup.state !== "idle" && (
              <div className="setup-progress">
                <div className="muted small">{vs.setup.step} {vs.setup.file}</div>
                {vs.setup.state === "running" && <Progress value={vs.setup.percent / 100} label={`${vs.setup.percent}%(${fmtBytes(vs.setup.downloaded)} / ${fmtBytes(vs.setup.total)})`} />}
                {vs.setup.state === "done" && <div className="ok-text">導入が完了しました。「起動」を押すか、書類を取り込むと自動で起動します。</div>}
                {vs.setup.error && <div className="error-text">{vs.setup.error}</div>}
              </div>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}

// ── 読取ルール ─────────────────────────────────────────────────────────
function ExtractionTab({ st, set, save, busy }: { st: Settings; set: (s: string, p: Settings) => void; save: () => void; busy: boolean }) {
  const ex = st.extraction;
  const m = st.matching;
  return (
    <div className="grid-2 top">
      <Card title="書類の読み方">
        <Field label="自社名(注文を受ける側)" hint="1行に1つ。宛先にある自社を発注元と取り違えないために使います。">
          <LinesInput value={ex.own_company_names} onChange={(v) => set("extraction", { own_company_names: v })} rows={3} placeholder="例: 株式会社サンプル" />
        </Field>
        <Field label="注文書以外として扱う表題の語" hint="表題にこの語があり、注文系の語が無い書類は「対象外」にします。">
          <LinesInput value={ex.skip_title_words} onChange={(v) => set("extraction", { skip_title_words: v })} rows={3} />
        </Field>
        <Field label="注文系の語(これを含む表題は注文書として扱う)">
          <LinesInput value={ex.order_title_words} onChange={(v) => set("extraction", { order_title_words: v })} rows={2} />
        </Field>
        <div className="row gap wrap">
          <Field label="発注元を読むページ数"><input type="number" min={1} max={5} value={ex.header_pages} onChange={(e) => set("extraction", { header_pages: Number(e.target.value) })} /></Field>
        </div>
        <Toggle checked={ex.use_text_layer} onChange={(v) => set("extraction", { use_text_layer: v })} label="電子 PDF の文字情報で注文番号などを検証する" />
        <Toggle checked={ex.orderer_fallback} onChange={(v) => set("extraction", { orderer_fallback: v })} label="発注元が読めない時に発注元だけを狙って読み直す" />
        <Toggle checked={ex.skip_duplicates} onChange={(v) => set("extraction", { skip_duplicates: v })} label="同じファイルの二重取込を防ぐ" />
      </Card>
      <Card title="明細の整え方・照合">
        <Field label="送料とみなす語"><LinesInput value={ex.shipping_keywords} onChange={(v) => set("extraction", { shipping_keywords: v })} rows={3} /></Field>
        <Field label="送料の商品コード"><input value={ex.shipping_code} onChange={(e) => set("extraction", { shipping_code: e.target.value })} /></Field>
        <Field label="集計行とみなす語(明細から除く)"><LinesInput value={ex.summary_keywords} onChange={(v) => set("extraction", { summary_keywords: v })} rows={3} /></Field>
        <Field label="型番の前に付く管理用の接頭辞(取り除いて照合)"><LinesInput value={ex.internal_code_prefixes} onChange={(v) => set("extraction", { internal_code_prefixes: v })} rows={2} /></Field>
        <Field label="型番の略記(略記=正式表記)"><KeyValueInput value={ex.code_abbreviations} onChange={(v) => set("extraction", { code_abbreviations: v })} /></Field>
        <Toggle checked={ex.include_master_in_prompt} onChange={(v) => set("extraction", { include_master_in_prompt: v })} label="商品マスタを VLM への指示に含める(品目数が上限以下の時)" />
        <div className="row gap wrap">
          <Field label="指示に含める品目数の上限"><input type="number" value={ex.max_master_items_in_prompt} onChange={(e) => set("extraction", { max_master_items_in_prompt: Number(e.target.value) })} /></Field>
          <Field label="型番あいまい一致の閾値"><input type="number" step="0.01" value={m.product_fuzzy_ratio} onChange={(e) => set("matching", { product_fuzzy_ratio: Number(e.target.value) })} /></Field>
          <Field label="納品先の確定に必要な根拠点"><input type="number" step="0.1" value={m.customer_min_score} onChange={(e) => set("matching", { customer_min_score: Number(e.target.value) })} /></Field>
        </div>
        <Toggle checked={st.learning.enabled} onChange={(v) => set("learning", { enabled: v })} label="人の修正を学習して次回から自動で直す" />
        <Field label="CSV の文字コード">
          <select value={st.export.csv_encoding} onChange={(e) => set("export", { csv_encoding: e.target.value })}>
            <option value="utf-8-sig">UTF-8(BOM 付き・Excel 対応)</option>
            <option value="cp932">Shift_JIS(古いシステム向け)</option>
          </select>
        </Field>
        <button className="btn primary" disabled={busy} onClick={save}>保存</button>
      </Card>
    </div>
  );
}

// ── 監視フォルダ ───────────────────────────────────────────────────────
function WatchTab({ st, set, save, busy }: { st: Settings; set: (s: string, p: Settings) => void; save: () => void; busy: boolean }) {
  const w = st.watch;
  const folders: { path: string; recursive: boolean; polling: boolean }[] = w.folders ?? [];
  const setFolders = (f: typeof folders) => set("watch", { folders: f });
  return (
    <Card title="監視フォルダ">
      <p className="muted">ホーム画面で監視モードを ON にすると、ここに登録したフォルダに置かれた書類を自動で取り込みます。ネットワーク共有は「ポーリング」を有効にしてください。</p>
      {folders.map((f, i) => (
        <div key={i} className="row gap wrap folder-row">
          <input className="grow mono" value={f.path} placeholder="例: C:\注文書\受信 または \\fileserver\share\orders"
            onChange={(e) => setFolders(folders.map((x, j) => (j === i ? { ...x, path: e.target.value } : x)))} />
          <Toggle checked={f.recursive} onChange={(v) => setFolders(folders.map((x, j) => (j === i ? { ...x, recursive: v } : x)))} label="サブフォルダも" />
          <Toggle checked={f.polling} onChange={(v) => setFolders(folders.map((x, j) => (j === i ? { ...x, polling: v } : x)))} label="ポーリング" />
          <button className="icon-btn" onClick={() => setFolders(folders.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <button className="btn small" onClick={() => setFolders([...folders, { path: "", recursive: false, polling: false }])}>＋ フォルダを追加</button>
      <div className="row gap wrap">
        <Field label="取込後のファイル">
          <select value={w.after} onChange={(e) => set("watch", { after: e.target.value })}>
            <option value="move">_eagle_done フォルダへ移動(推奨)</option>
            <option value="keep">そのまま残す</option>
            <option value="delete">削除する(原本はアプリ内に保存済み)</option>
          </select>
        </Field>
      </div>
      <Field label="対象の拡張子"><LinesInput value={w.extensions} onChange={(v) => set("watch", { extensions: v })} rows={3} /></Field>
      <button className="btn primary" disabled={busy} onClick={save}>保存</button>
    </Card>
  );
}

// ── 追加項目 ───────────────────────────────────────────────────────────
function FieldsTab({ isAdmin }: { isAdmin: boolean }) {
  const [data, setData] = useState<FieldsPayload | null>(null);
  const [draft, setDraft] = useState({ key: "", label: "", level: "header", kind: "text", description: "" });
  const { busy, run } = useAction();
  const load = () => api.get<FieldsPayload>("/api/fields").then(setData);
  useEffect(() => { load(); }, []);
  return (
    <div className="grid-2 top">
      <Card title="追加項目(ユーザー定義)">
        <p className="muted">標準の項目以外に読み取りたい項目を追加できます(例: 案件名・現場名・支払期日)。VLM には「読み取り指示」が渡されます。</p>
        {data?.custom?.length ? (
          <table className="table compact">
            <thead><tr><th>キー</th><th>表示名</th><th>単位</th><th>型</th><th>有効</th><th /></tr></thead>
            <tbody>
              {data.custom.map((f) => (
                <tr key={f.id}>
                  <td className="mono">{f.key}</td><td>{f.label}</td><td>{f.level === "line" ? "明細" : "書類"}</td><td>{f.kind}</td>
                  <td><Toggle checked={f.enabled} disabled={!isAdmin} onChange={(v) => run(() => api.put(`/api/fields/${f.id}`, { enabled: v }).then(load))} /></td>
                  <td>{isAdmin && <button className="icon-btn" onClick={() => confirm(`${f.label} を削除しますか?`) && run(() => api.del(`/api/fields/${f.id}`).then(load))}>×</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty>追加項目はありません。</Empty>}
      </Card>
      {isAdmin && (
        <Card title="項目を追加">
          <div className="row gap wrap">
            <Field label="キー(英小文字)"><input className="mono" value={draft.key} placeholder="project_name" onChange={(e) => setDraft({ ...draft, key: e.target.value })} /></Field>
            <Field label="表示名"><input value={draft.label} placeholder="案件名" onChange={(e) => setDraft({ ...draft, label: e.target.value })} /></Field>
          </div>
          <div className="row gap wrap">
            <Field label="単位"><select value={draft.level} onChange={(e) => setDraft({ ...draft, level: e.target.value })}><option value="header">書類ごと</option><option value="line">明細の行ごと</option></select></Field>
            <Field label="型"><select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}><option value="text">文字</option><option value="number">数値</option><option value="date">日付</option></select></Field>
          </div>
          <Field label="読み取り指示(VLM へ)" hint="例: 『件名』または『案件名』欄に書かれた工事・案件の名称"><textarea rows={2} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
          <button className="btn primary" disabled={busy || !draft.key} onClick={() => run(() => api.post("/api/fields", draft).then(() => { setDraft({ key: "", label: "", level: "header", kind: "text", description: "" }); load(); }), "追加しました")}>追加</button>
        </Card>
      )}
    </div>
  );
}

// ── 外部DB連携 ─────────────────────────────────────────────────────────
interface Target { id: number; name: string; url: string; header_table: string; line_table: string; mapping: Settings; create_tables: boolean; trigger: string; enabled: boolean; last_error: string; last_push_at: string | null }

function IntegrationsTab({ isAdmin }: { isAdmin: boolean }) {
  const [data, setData] = useState<{ targets: Target[]; default_mapping: Settings } | null>(null);
  const [edit, setEdit] = useState<Partial<Target> | null>(null);
  const load = () => api.get<{ targets: Target[]; default_mapping: Settings }>("/api/integrations").then(setData).catch(() => undefined);
  useEffect(() => { if (isAdmin) load(); }, [isAdmin]);
  if (!isAdmin) return <Empty>管理者のみ利用できます。</Empty>;
  return (
    <Card title="外部データベースへの書き出し" actions={<button className="btn primary small" onClick={() => setEdit({ name: "", url: "", header_table: "eagle_orders", line_table: "eagle_order_lines", create_tables: true, trigger: "manual", enabled: true, mapping: data?.default_mapping })}>＋ 連携先を追加</button>}>
      <p className="muted">アプリ内の DB とは別に、既存の業務システムの DB や共有 DB へ書き出せます(PostgreSQL / MySQL / SQL Server / SQLite / Access 等)。列の対応を変えれば既存のテーブルにも書き込めます。</p>
      {data?.targets.length ? (
        <table className="table compact">
          <thead><tr><th>名前</th><th>接続先</th><th>タイミング</th><th>最終書き出し</th><th>状態</th><th /></tr></thead>
          <tbody>
            {data.targets.map((t) => (
              <tr key={t.id}>
                <td className="strong">{t.name}</td>
                <td className="mono small ellipsis">{t.url}</td>
                <td>{{ manual: "手動", confirm: "確定時", extract: "読取完了時" }[t.trigger]}</td>
                <td className="muted">{fmtDate(t.last_push_at)}</td>
                <td>{t.last_error ? <span className="error-text small" title={t.last_error}>エラー</span> : t.enabled ? <span className="tag ok">有効</span> : <span className="tag">無効</span>}</td>
                <td><button className="btn small" onClick={() => setEdit(t)}>編集</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <Empty>連携先はありません。</Empty>}
      {edit && <TargetDialog target={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
    </Card>
  );
}

function TargetDialog({ target, onClose, onSaved }: { target: Partial<Target>; onClose: () => void; onSaved: () => void }) {
  const [t, setT] = useState(target);
  const [mapping, setMapping] = useState(JSON.stringify(target.mapping ?? {}, null, 2));
  const [result, setResult] = useState("");
  const { busy, run } = useAction();
  const body = () => {
    let m: Settings;
    try { m = JSON.parse(mapping); } catch { throw new Error("列の対応(JSON)の形式が正しくありません"); }
    return { ...t, mapping: m } as Record<string, unknown>;
  };
  return (
    <Modal title={t.id ? "連携先を編集" : "連携先を追加"} onClose={onClose} width={760}
      footer={<>
        {t.id && <button className="btn danger ghost" onClick={() => confirm("削除しますか?") && run(() => api.del(`/api/integrations/${t.id}`).then(onSaved))}>削除</button>}
        <span className="spacer" />
        <button className="btn" disabled={busy} onClick={async () => {
          const r = await run(() => api.post<{ ok: boolean; error?: string; tables?: string[] }>("/api/integrations/test", { id: t.id, url: t.url }));
          if (r) setResult(r.ok ? `接続 OK(テーブル ${r.tables?.length ?? 0} 個)` : `失敗: ${r.error}`);
        }}>接続テスト</button>
        <button className="btn primary" disabled={busy} onClick={() => run(() => (t.id ? api.put(`/api/integrations/${t.id}`, body()) : api.post("/api/integrations", body())).then(onSaved), "保存しました")}>保存</button>
      </>}>
      <Field label="名前"><input value={t.name ?? ""} onChange={(e) => setT({ ...t, name: e.target.value })} /></Field>
      <Field label="接続 URL(SQLAlchemy 形式)" hint="例: postgresql+psycopg://user:pass@host/db / mysql+pymysql://user:pass@host/db / sqlite:///C:/data/orders.db">
        <input className="mono" value={t.url ?? ""} onChange={(e) => setT({ ...t, url: e.target.value })} />
      </Field>
      {result && <div className={result.startsWith("接続 OK") ? "ok-text" : "error-text"}>{result}</div>}
      <div className="row gap wrap">
        <Field label="注文書テーブル"><input value={t.header_table ?? ""} onChange={(e) => setT({ ...t, header_table: e.target.value })} /></Field>
        <Field label="明細テーブル"><input value={t.line_table ?? ""} onChange={(e) => setT({ ...t, line_table: e.target.value })} /></Field>
        <Field label="書き出すタイミング">
          <select value={t.trigger} onChange={(e) => setT({ ...t, trigger: e.target.value })}>
            <option value="manual">手動(一覧で選んで書き出し)</option><option value="confirm">確定した時</option><option value="extract">読取が終わった時</option>
          </select>
        </Field>
      </div>
      <div className="row gap wrap">
        <Toggle checked={!!t.create_tables} onChange={(v) => setT({ ...t, create_tables: v })} label="テーブルが無ければ作成する" />
        <Toggle checked={!!t.enabled} onChange={(v) => setT({ ...t, enabled: v })} label="有効" />
      </div>
      <Field label="列の対応(JSON: アプリの項目 → 書き出し先の列名)" hint="不要な項目は削除、既存テーブルに合わせて右側の列名を変更してください。">
        <textarea className="mono small" rows={12} value={mapping} onChange={(e) => setMapping(e.target.value)} />
      </Field>
      {t.last_error && <div className="notice bad small">前回のエラー: {t.last_error}</div>}
    </Modal>
  );
}

// ── 利用者 ─────────────────────────────────────────────────────────────
interface UserRow { id: number; username: string; display_name: string; role: string; active: boolean; last_login: string | null }

function UsersTab({ info }: { info: Info }) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [draft, setDraft] = useState({ username: "", display_name: "", password: "", role: "editor" });
  const [pw, setPw] = useState({ current: "", new: "" });
  const { busy, run } = useAction();
  const isAdmin = info.user?.role === "admin";
  const load = async () => { if (isAdmin) await api.get<UserRow[]>("/api/users").then(setUsers).catch(() => undefined); };
  useEffect(() => { load(); }, []);
  return (
    <div className="grid-2 top">
      {isAdmin && (
        <Card title="利用者">
          <table className="table compact">
            <thead><tr><th>ユーザー名</th><th>表示名</th><th>権限</th><th>有効</th><th>最終ログイン</th><th /></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td className="mono">{u.username}</td><td>{u.display_name}</td>
                  <td>
                    <select value={u.role} onChange={(e) => run(() => api.put(`/api/users/${u.id}`, { role: e.target.value }).then(load))}>
                      <option value="admin">管理者</option><option value="editor">編集者</option><option value="viewer">閲覧者</option>
                    </select>
                  </td>
                  <td><Toggle checked={u.active} onChange={(v) => run(() => api.put(`/api/users/${u.id}`, { active: v }).then(load))} /></td>
                  <td className="muted">{fmtDate(u.last_login)}</td>
                  <td>
                    <button className="btn small ghost" onClick={() => { const p = prompt(`${u.username} の新しいパスワード(8文字以上)`); if (p) run(() => api.put(`/api/users/${u.id}`, { password: p }), "変更しました"); }}>PW変更</button>
                    {u.id !== info.user?.id && <button className="icon-btn" onClick={() => confirm(`${u.username} を削除しますか?`) && run(() => api.del(`/api/users/${u.id}`).then(load))}>×</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <h4>追加</h4>
          <div className="row gap wrap">
            <input placeholder="ユーザー名" value={draft.username} onChange={(e) => setDraft({ ...draft, username: e.target.value })} />
            <input placeholder="表示名" value={draft.display_name} onChange={(e) => setDraft({ ...draft, display_name: e.target.value })} />
            <input type="password" placeholder="パスワード(8文字以上)" value={draft.password} onChange={(e) => setDraft({ ...draft, password: e.target.value })} />
            <select value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value })}>
              <option value="admin">管理者</option><option value="editor">編集者</option><option value="viewer">閲覧者</option>
            </select>
            <button className="btn primary" disabled={busy} onClick={() => run(() => api.post("/api/users", draft).then(() => { setDraft({ username: "", display_name: "", password: "", role: "editor" }); load(); }), "追加しました")}>追加</button>
          </div>
          <p className="muted small">管理者: すべての操作 / 編集者: 取込・修正・確定・テンプレート / 閲覧者: 閲覧と出力のみ</p>
        </Card>
      )}
      <Card title="自分のパスワードを変更">
        <Field label="現在のパスワード"><input type="password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></Field>
        <Field label="新しいパスワード"><input type="password" value={pw.new} onChange={(e) => setPw({ ...pw, new: e.target.value })} /></Field>
        <button className="btn primary" disabled={busy || !pw.new} onClick={() => run(() => api.post("/api/auth/password", pw).then(() => setPw({ current: "", new: "" })), "変更しました")}>変更</button>
      </Card>
    </div>
  );
}

// ── ログ ───────────────────────────────────────────────────────────────
interface Ev { id: number; ts: string; level: string; kind: string; document_id: number | null; username: string; message: string }

function LogsTab() {
  const [items, setItems] = useState<Ev[]>([]);
  const [level, setLevel] = useState("");
  const load = () => api.get<Ev[]>(`/api/events?limit=300${level ? `&level=${level}` : ""}`).then(setItems).catch(() => undefined);
  useEffect(() => { load(); }, [level]);
  return (
    <Card title="操作・処理ログ" actions={
      <select value={level} onChange={(e) => setLevel(e.target.value)}><option value="">すべて</option><option value="error">エラーのみ</option></select>
    }>
      <div className="table-wrap">
        <table className="table compact">
          <tbody>
            {items.map((e) => (
              <tr key={e.id} className={e.level === "error" ? "row-error" : ""}>
                <td className="muted nowrap">{fmtDate(e.ts)}</td>
                <td><span className="tag">{e.kind}</span></td>
                <td>{e.message}{e.document_id ? <> <a className="link" href={`#/documents/${e.document_id}`}>#{e.document_id}</a></> : null}</td>
                <td className="muted">{e.username}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
