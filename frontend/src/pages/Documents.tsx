import { useEffect, useMemo, useState } from "react";
import { api, download, qs } from "../api";
import { Card, Empty, StatusBadge, fmtDate, fmtNum, navigate, useAction, useInterval } from "../components/ui";
import type { DocSummary, Info, Status } from "../types";

interface ListRes { items: DocSummary[]; total: number; page: number; page_size: number; counts: Partial<Record<Status, number>> }
interface Target { id: number; name: string; trigger: string }

const FILTERS: { key: string; label: string }[] = [
  { key: "", label: "すべて" },
  { key: "needs_review", label: "要確認" },
  { key: "extracted", label: "抽出済" },
  { key: "confirmed", label: "確定" },
  { key: "queued,processing", label: "処理待ち・処理中" },
  { key: "failed", label: "失敗" },
  { key: "skipped", label: "対象外" },
];

export default function Documents({ info, tick }: { info: Info; tick: number }) {
  const [status, setStatus] = useState(() => {
    const s = sessionStorage.getItem("eagle.status") ?? "";
    sessionStorage.removeItem("eagle.status");
    return s;
  });
  const [q, setQ] = useState("");
  const [qInput, setQInput] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<ListRes | null>(null);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [targets, setTargets] = useState<Target[]>([]);
  const { busy, run } = useAction();
  const canEdit = info.user?.role !== "viewer";

  const params = useMemo(() => ({ status, q, date_from: dateFrom, date_to: dateTo }), [status, q, dateFrom, dateTo]);
  const load = () => api.get<ListRes>("/api/documents" + qs({ ...params, page, page_size: 50 })).then(setData).catch(() => undefined);

  useEffect(() => { load(); }, [params, page, tick]);
  useEffect(() => { setPage(1); setSel(new Set()); }, [params]);
  useInterval(() => {
    if (data?.items.some((d) => d.status === "queued" || d.status === "processing")) load();
  }, 3000);
  useEffect(() => {
    if (info.user?.role === "admin") api.get<{ targets: Target[] }>("/api/integrations").then((r) => setTargets(r.targets)).catch(() => undefined);
  }, [info.user?.role]);

  const ids = [...sel];
  const allOnPage = data?.items.length ? data.items.every((d) => sel.has(d.id)) : false;
  const toggleAll = () => setSel(allOnPage ? new Set() : new Set(data?.items.map((d) => d.id)));
  const toggle = (id: number) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const bulk = (action: string, label: string) => {
    if (action === "delete" && !confirm(`${ids.length}件を削除します。よろしいですか?`)) return;
    run(() => api.post("/api/documents/bulk", { ids, action }).then(() => { setSel(new Set()); load(); }), `${label}しました`);
  };
  const exportUrl = (format: string, layout = "flat") =>
    "/api/export" + qs(ids.length ? { format, layout, ids: ids.join(",") } : { format, layout, ...params });

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="page">
      <header className="page-head">
        <h1>注文書</h1>
        <div className="row gap">
          <span className="muted">{ids.length ? `${ids.length}件選択中` : `表示中の条件で ${data?.total ?? 0}件`}を出力:</span>
          <button className="btn" onClick={() => download(exportUrl("xlsx"))}>Excel (.xlsx)</button>
          <button className="btn" onClick={() => download(exportUrl("csv", "flat"))}>CSV(明細展開)</button>
          <button className="btn ghost" onClick={() => download(exportUrl("csv", "orders"))}>CSV(注文書のみ)</button>
        </div>
      </header>

      <div className="filters">
        <div className="seg">
          {FILTERS.map((f) => {
            const n = f.key ? f.key.split(",").reduce((a, k) => a + (data?.counts[k as Status] ?? 0), 0) : undefined;
            return (
              <button key={f.key} className={status === f.key ? "active" : ""} onClick={() => setStatus(f.key)}>
                {f.label}{n !== undefined && <span className="count">{n}</span>}
              </button>
            );
          })}
        </div>
        <form className="row gap" onSubmit={(e) => { e.preventDefault(); setQ(qInput); }}>
          <input className="search" placeholder="発注元・注文番号・品名・ファイル名で検索" value={qInput} onChange={(e) => setQInput(e.target.value)} />
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} title="取込日(から)" />
          <span className="muted">〜</span>
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} title="取込日(まで)" />
          <button className="btn">検索</button>
        </form>
      </div>

      {canEdit && ids.length > 0 && (
        <div className="bulkbar">
          <span>{ids.length}件を選択</span>
          <button className="btn" disabled={busy} onClick={() => bulk("confirm", "確定")}>確定</button>
          <button className="btn" disabled={busy} onClick={() => bulk("reprocess", "再処理を依頼")}>再処理</button>
          {targets.map((t) => (
            <button key={t.id} className="btn" disabled={busy}
              onClick={() => run(() => api.post(`/api/integrations/${t.id}/push`, { ids }).then(load), `${t.name} へ書き出しました`)}>
              {t.name} へ書き出し
            </button>
          ))}
          <button className="btn danger" disabled={busy} onClick={() => bulk("delete", "削除")}>削除</button>
          <button className="btn ghost" onClick={() => setSel(new Set())}>選択解除</button>
        </div>
      )}

      <Card>
        {data && data.items.length === 0 ? (
          <Empty>該当する書類はありません。</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th className="chk"><input type="checkbox" checked={allOnPage} onChange={toggleAll} /></th>
                  <th>ID</th>
                  <th>状態</th>
                  <th>ファイル名</th>
                  <th>発注元</th>
                  <th>注文番号</th>
                  <th>注文日</th>
                  <th className="num">明細</th>
                  <th className="num">金額</th>
                  <th>テンプレート</th>
                  <th>取込日時</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((d) => (
                  <tr key={d.id} className={`clickable ${sel.has(d.id) ? "selected" : ""}`} onClick={() => navigate(`#/documents/${d.id}`)}>
                    <td className="chk" onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={sel.has(d.id)} onChange={() => toggle(d.id)} />
                    </td>
                    <td className="muted">{d.id}</td>
                    <td>
                      <StatusBadge status={d.status} />
                      {d.warnings > 0 && d.status !== "confirmed" && <span className="warn-count" title="要確認の項目">{d.warnings}</span>}
                    </td>
                    <td className="ellipsis" title={d.filename}>{d.filename}</td>
                    <td className="ellipsis" title={d.buyer_company}>{d.buyer_company}</td>
                    <td className="mono">{d.order_number}</td>
                    <td className="nowrap">{d.order_date}</td>
                    <td className="num">{d.line_count || ""}</td>
                    <td className="num">{fmtNum(d.amount)}</td>
                    <td className="ellipsis">{d.template}</td>
                    <td className="muted nowrap">{fmtDate(d.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {pages > 1 && (
          <div className="pagination">
            <button className="btn small" disabled={page <= 1} onClick={() => setPage(page - 1)}>前へ</button>
            <span>{page} / {pages}</span>
            <button className="btn small" disabled={page >= pages} onClick={() => setPage(page + 1)}>次へ</button>
          </div>
        )}
      </Card>
    </div>
  );
}
