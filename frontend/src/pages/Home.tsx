import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Card, Empty, Progress, StatusBadge, Toggle, fmtDate, navigate, useAction, useInterval } from "../components/ui";
import type { Info, JobsStatus, Status, WatchStatus } from "../types";

interface Dashboard {
  counts: Partial<Record<Status, number>>;
  recent: { id: number; filename: string; status: Status; buyer_company: string; created_at: string }[];
  jobs: JobsStatus;
  watch: WatchStatus;
  vlm: { backend: string; llama: { state: string; error: string; model: string } };
}

const VLM_STATE: Record<string, string> = {
  ready: "準備完了", starting: "起動中(モデル読込中)", stopped: "停止中", error: "エラー", external: "接続中(外部)",
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
  const vlmState = d?.vlm.backend === "llama_cpp" ? (llama?.state ?? "") : "external";

  return (
    <div className="page">
      <header className="page-head">
        <h1>ホーム</h1>
        <div className={`vlm-pill vlm-${vlmState}`} title={llama?.error || ""} onClick={() => navigate("#/settings/vlm")}>
          <span className="dot" />
          VLM: {d?.vlm.backend === "llama_cpp" ? `${VLM_STATE[vlmState] ?? vlmState}${llama?.model ? ` / ${llama.model}` : ""}` : d?.vlm.backend}
        </div>
      </header>

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
          <div className="muted">待ち: {d?.jobs.queued ?? 0} 件</div>
          {vlmState === "starting" && <div className="notice">VLM を準備中です。最初の書類は読込完了まで待ってから処理されます。</div>}
          {vlmState === "error" && (
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
