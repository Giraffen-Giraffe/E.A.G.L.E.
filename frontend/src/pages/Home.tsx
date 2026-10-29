import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Card, Empty, Progress, StatusBadge, Toggle, fmtBytes, fmtDate, navigate, useAction, useInterval } from "../components/ui";
import type { Info, JobsStatus, Status, WatchStatus } from "../types";

interface Dashboard {
  counts: Partial<Record<Status, number>>;
  recent: { id: number; filename: string; status: Status; buyer_company: string; created_at: string }[];
  jobs: JobsStatus;
  watch: WatchStatus;
  own_company_set: boolean;
  vlm: {
    backend: string;
    llama: { state: string; error: string; model: string; needs_setup: boolean };
    setup: { state: string; step: string; file: string; percent: number; downloaded: number; total: number; error: string };
    presets: Record<string, { label: string; size_gb: number }>;
    recommended: string;
  };
}

const VLM_STATE: Record<string, string> = {
  ready: "準備完了", starting: "起動中(モデル読込中)", stopped: "停止中", error: "エラー", external: "接続中(外部)",
  setup: "未導入",
};

export default function Home({ info, onUpload, tick }: { info: Info; onUpload: (f: File[]) => void; tick: number }) {
  const [d, setD] = useState<Dashboard | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const { busy, run } = useAction();
  const canEdit = info.user?.role !== "viewer";

  const load = () => api.get<Dashboard>("/api/dashboard").then(setD).catch(() => undefined);
  useEffect(() => { load(); }, [tick]);
  useInterval(load, 2500);

  const toggleWatch = (on: boolean) =>
    run(() => api.post(`/api/watch/${on ? "start" : "stop"}`).then(load), on ? "監視を開始しました" : "監視を停止しました");

  const c = d?.counts ?? {};
  const job = d?.jobs.running;
  const llama = d?.vlm.llama;
  const vlmState = d?.vlm.backend !== "llama_cpp" ? "external" : llama?.needs_setup ? "setup" : (llama?.state ?? "");

  return (
    <div className="page">
      <header className="page-head">
        <h1>ホーム</h1>
        <div className={`vlm-pill vlm-${vlmState}`} title={llama?.error || ""} onClick={() => navigate("#/settings/vlm")}>
          <span className="dot" />
          VLM: {d?.vlm.backend === "llama_cpp" ? `${VLM_STATE[vlmState] ?? vlmState}${llama?.model ? ` / ${llama.model}` : ""}` : d?.vlm.backend}
        </div>
      </header>

      {d && d.vlm.backend === "llama_cpp" && (d.vlm.llama.needs_setup || d.vlm.setup.state === "running") && (
        <SetupCard vlm={d.vlm} canSetup={info.user?.role === "admin"} onStarted={load} />
      )}

      {d && !d.own_company_set && info.user?.role === "admin" && <OwnCompanyCard onSaved={load} />}

      <div className="grid-2">
        <Card title="注文書を取り込む">
          <div
            className={`dropzone ${canEdit ? "" : "disabled"}`}
            onClick={() => canEdit && fileRef.current?.click()}
            role="button"
            tabIndex={0}
          >
            <div className="dz-icon">⇪</div>
            <div className="dz-main">ここにドラッグ&ドロップ、またはクリックして選択</div>
            <div className="dz-sub">PDF / 画像(PNG・JPEG・TIFF) / メール(.eml) / Excel・CSV・テキスト ── 画面のどこにドロップしても取り込めます</div>
            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.gif,.eml,.txt,.csv,.tsv,.xlsx,.xlsm"
              onChange={(e) => { onUpload(Array.from(e.target.files ?? [])); e.target.value = ""; }}
            />
          </div>
        </Card>

        <Card
          title="監視モード"
          actions={<Toggle checked={!!d?.watch.running} onChange={toggleWatch} disabled={busy || !canEdit} label={d?.watch.running ? "ON" : "OFF"} />}
        >
          {d?.watch.folders.length ? (
            <ul className="folder-list">
              {d.watch.folders.map((f) => (
                <li key={f.path} className={f.ok ? "" : "bad"}>
                  <code>{f.path}</code>
                  {f.recursive && <span className="tag">サブフォルダ</span>}
                  {f.polling && <span className="tag">ポーリング</span>}
                  {f.error && <span className="error-text"> {f.error}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">
              {d?.watch.running ? "監視中です。" : "監視モードを ON にすると、"}
              指定フォルダに置かれた注文書を自動で取り込みます。
            </p>
          )}
          <div className="row gap">
            <span className="muted">取込済 {d?.watch.processed ?? 0} 件{d?.watch.pending ? ` / 待ち ${d.watch.pending} 件` : ""}</span>
            <a href="#/settings/watch" className="link">監視フォルダの設定 →</a>
          </div>
          {d?.watch.errors.map((e, i) => <div key={i} className="error-text small">{e}</div>)}
        </Card>
      </div>

      <div className="stat-row">
        {(["needs_review", "extracted", "confirmed", "failed", "skipped"] as Status[]).map((s) => (
          <a key={s} className={`stat st-${s}`} href="#/documents" onClick={() => sessionStorage.setItem("eagle.status", s)}>
            <div className="stat-num">{c[s] ?? 0}</div>
            <div className="stat-label"><StatusBadge status={s} /></div>
          </a>
        ))}
      </div>

      <div className="grid-2">
        <Card title="処理状況">
          {job ? (
            <div className="job-now">
              <div className="row between">
                <a className="link" href={`#/documents/${job.document_id}`}>書類 #{job.document_id}</a>
                <span className="muted">{Math.round(job.elapsed)}秒経過</span>
              </div>
              <Progress value={job.progress} label={job.message} />
            </div>
          ) : (
            <p className="muted">処理中の書類はありません。</p>
          )}
          <div className="muted">待ち: {d?.jobs.queued ?? 0} 件{d?.jobs.waiting ? `(${d.jobs.waiting}。導入が済むと自動で読み取ります)` : ""}</div>
          {vlmState === "starting" && <div className="notice">VLM を準備中です。最初の書類は読込完了まで待ってから処理されます。</div>}
          {vlmState === "error" && !llama?.needs_setup && (
            <div className="notice bad">
              VLM を起動できません: {llama?.error} <a className="link" href="#/settings/vlm">VLM 設定へ</a>
            </div>
          )}
        </Card>
        <Card title="最近の書類" actions={<a className="link" href="#/documents">すべて表示 →</a>}>
          {d?.recent.length ? (
            <table className="table compact">
              <tbody>
                {d.recent.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => navigate(`#/documents/${r.id}`)}>
                    <td><StatusBadge status={r.status} /></td>
                    <td className="ellipsis">{r.filename}</td>
                    <td className="ellipsis">{r.buyer_company}</td>
                    <td className="muted nowrap">{fmtDate(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>まだ書類がありません。注文書をドロップしてください。</Empty>
          )}
        </Card>
      </div>
    </div>
  );
}

/** 初回: 読み取りエンジン(llama.cpp + モデル)が未導入の時の案内。 */
function SetupCard({ vlm, canSetup, onStarted }: { vlm: Dashboard["vlm"]; canSetup: boolean; onStarted: () => void }) {
  const [preset, setPreset] = useState(vlm.recommended || "standard");
  const { busy, run } = useAction();
  const st = vlm.setup;
  const running = st.state === "running";
  return (
    <Card title="はじめに: 読み取りエンジン(VLM)を導入してください" className="setup-card">
      <p>
        注文書を読み取るための AI モデル(Qwen3-VL)と実行環境(llama.cpp)を、初回だけダウンロードします。
        導入後はインターネットに接続しなくても動作し、書類が外部へ送られることはありません。
      </p>
      {running ? (
        <div className="setup-progress">
          <div className="muted small">{st.step} {st.file}</div>
          <Progress value={st.percent / 100} label={`${st.percent}%(${fmtBytes(st.downloaded)} / ${fmtBytes(st.total)})`} />
          <div className="muted small">ダウンロード中もアプリは使えます。完了すると自動で準備されます。</div>
        </div>
      ) : canSetup ? (
        <div className="row gap wrap">
          <select value={preset} onChange={(e) => setPreset(e.target.value)}>
            {Object.entries(vlm.presets).map(([k, p]) => (
              <option key={k} value={k}>{p.label}(約 {p.size_gb} GB){k === vlm.recommended ? " ← お使いの PC に推奨" : ""}</option>
            ))}
          </select>
          <button className="btn primary" disabled={busy}
            onClick={() => run(() => api.post("/api/vlm/setup", { preset }).then(onStarted))}>導入する</button>
          <a className="link" href="#/settings/vlm">詳しい設定</a>
        </div>
      ) : (
        <div className="muted">導入は管理者が行います。</div>
      )}
      {st.state === "error" && <div className="error-text">導入に失敗しました: {st.error}(もう一度「導入する」を押すと続きから再開します)</div>}
    </Card>
  );
}

/** 初回: 自社名(注文を受ける側)の登録。宛先の自社を発注元と取り違えないために使う。 */
function OwnCompanyCard({ onSaved }: { onSaved: () => void }) {
  const [names, setNames] = useState("");
  const { busy, run } = useAction();
  const list = names.split(/[,、，]+/).map((x) => x.trim()).filter(Boolean);
  return (
    <Card title="はじめに: 自社名を登録してください" className="setup-card">
      <p>
        注文書の宛先(「〜御中」)に書かれるあなたの会社名です。登録すると、宛先の自社を発注元と取り違えなくなり、
        読み取りの精度が上がります。あとから 設定 → 読取ルール で変更できます。
      </p>
      <div className="row gap wrap">
        <input className="grow" placeholder="例: 株式会社サンプル(複数ある場合は読点で区切る)" value={names} onChange={(e) => setNames(e.target.value)} />
        <button className="btn primary" disabled={busy || !list.length}
          onClick={() => run(() => api.put("/api/settings", { extraction: { own_company_names: list } }).then(onSaved), "自社名を登録しました")}>
          登録
        </button>
      </div>
    </Card>
  );
}
