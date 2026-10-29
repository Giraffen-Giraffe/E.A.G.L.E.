import { useEffect, useState } from "react";
import { api } from "../api";
import { Card, Empty, Toggle, fmtDate, useAction } from "../components/ui";

interface Stats {
  confirmed_docs: number;
  fields: { field: string; corrected: number; total: number; rate: number }[];
  templates: { template: string; corrected_docs: number; docs: number; accuracy: number | null }[];
  lines: { total: number; corrected: number; accuracy: number | null };
  rules: number;
}
interface Rule { id: number; template: string; field: string; original: string; corrected: string; count: number; enabled: boolean; updated_at: string }

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);

export default function Learning() {
  const [data, setData] = useState<{ stats: Stats; rules: Rule[] } | null>(null);
  const { run } = useAction();
  const load = () => api.get<{ stats: Stats; rules: Rule[] }>("/api/learning").then(setData).catch(() => undefined);
  useEffect(() => { load(); }, []);
  const s = data?.stats;

  return (
    <div className="page">
      <header className="page-head"><h1>学習</h1></header>
      <div className="notice">
        確定した書類で人が直した内容を記憶し、次回から自動で直します(値の置換・型番の読み替え・空欄の既定値・顧客照合の実績)。
        修正が多い項目は、テンプレートに ROI を追加すると読み取り自体の精度が上がります。
      </div>
      <div className="stat-row">
        <div className="stat"><div className="stat-num">{s?.confirmed_docs ?? 0}</div><div className="stat-label">確定した書類</div></div>
        <div className="stat"><div className="stat-num">{pct(s?.lines.accuracy)}</div><div className="stat-label">明細の無修正率</div></div>
        <div className="stat"><div className="stat-num">{s?.rules ?? 0}</div><div className="stat-label">学習したルール</div></div>
      </div>
      <div className="grid-2">
        <Card title="テンプレート別の無修正率">
          {s?.templates.length ? (
            <table className="table compact">
              <thead><tr><th>テンプレート</th><th className="num">書類</th><th className="num">無修正率</th></tr></thead>
              <tbody>{s.templates.map((t) => <tr key={t.template}><td>{t.template}</td><td className="num">{t.docs}</td><td className="num">{pct(t.accuracy)}</td></tr>)}</tbody>
            </table>
          ) : <Empty>確定した書類がまだありません。</Empty>}
        </Card>
        <Card title="修正の多い項目">
          {s?.fields.filter((f) => f.corrected > 0).length ? (
            <table className="table compact">
              <thead><tr><th>項目</th><th className="num">修正率</th><th /></tr></thead>
              <tbody>
                {s.fields.filter((f) => f.corrected > 0).slice(0, 12).map((f) => (
                  <tr key={f.field}>
                    <td className="mono">{f.field}</td>
                    <td className="num">{pct(f.rate)}({f.corrected}/{f.total})</td>
                    <td>{f.rate >= 0.2 && <span className="tag tag-changed">ROI 追加を推奨</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty>修正はまだありません。</Empty>}
        </Card>
      </div>
      <Card title={`学習したルール (${data?.rules.length ?? 0})`}>
        {data?.rules.length ? (
          <div className="table-wrap">
            <table className="table compact">
              <thead><tr><th>有効</th><th>範囲</th><th>項目</th><th>読取値</th><th>→ 修正後</th><th className="num">回数</th><th>更新</th><th /></tr></thead>
              <tbody>
                {data.rules.map((r) => (
                  <tr key={r.id} className={r.enabled ? "" : "muted"}>
                    <td><Toggle checked={r.enabled} onChange={(v) => run(() => api.put(`/api/learning/${r.id}`, { enabled: v }).then(load))} /></td>
                    <td>{r.template}</td>
                    <td className="mono">{r.field === "line.product_code" ? "明細の商品コード" : r.field}</td>
                    <td className="mono">{r.original || "(空欄)"}</td>
                    <td className="mono strong">{r.corrected}</td>
                    <td className="num">{r.count}</td>
                    <td className="muted">{fmtDate(r.updated_at)}</td>
                    <td><button className="icon-btn" title="削除" onClick={() => run(() => api.del(`/api/learning/${r.id}`).then(load))}>×</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>まだ学習したルールはありません。書類を確認・修正して「確定」すると学習します。</Empty>}
      </Card>
    </div>
  );
}
