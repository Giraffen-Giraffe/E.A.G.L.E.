import { useEffect, useRef, useState } from "react";
import { api, qs } from "../api";
import { Card, Empty, Tabs, Toggle, fmtNum, useAction } from "../components/ui";
import type { Info } from "../types";

type Kind = "products" | "customers";
type Row = Record<string, any>;

const HELP: Record<Kind, string> = {
  products: "列名の例: 商品コード(品番/型番) / 品名 / 単位 / 単価。取り込むと型番の誤読補正とマスタ照合が有効になります。",
  customers: "列名の例: 顧客コード / 会社名 / 部署 / 担当者 / 都道府県 / 市区町村 / 住所 / 郵便番号 / TEL。取り込むと発注元・納品先を顧客コードに照合します。",
};

export default function Masters({ info }: { info: Info }) {
  const [kind, setKind] = useState<Kind>("products");
  const [q, setQ] = useState("");
  const [data, setData] = useState<{ total: number; items: Row[] } | null>(null);
  const [replace, setReplace] = useState(true);
  const fileRef = useRef<HTMLInputElement>(null);
  const { busy, run } = useAction();
  const isAdmin = info.user?.role === "admin";

  const load = () => api.get<{ total: number; items: Row[] }>(`/api/masters/${kind}` + qs({ q })).then(setData).catch(() => undefined);
  useEffect(() => { load(); }, [kind]);

  const importFile = async (f: File) => {
    const fd = new FormData();
    fd.append("file", f, f.name);
    fd.append("replace", String(replace));
    const r = await run(() => api.post<{ imported: number; columns: Record<string, string> }>(`/api/masters/${kind}/import`, fd));
    if (r) {
      alert(`${r.imported}件を取り込みました。\n認識した列: ${Object.entries(r.columns).map(([k, v]) => `${k}←${v}`).join(", ")}`);
      load();
    }
  };

  return (
    <div className="page">
      <header className="page-head"><h1>マスタ</h1></header>
      <Tabs<Kind> value={kind} onChange={(k) => { setKind(k); setData(null); }} tabs={[{ key: "products", label: "商品マスタ" }, { key: "customers", label: "顧客マスタ" }]} />
      <div className="notice">{HELP[kind]} マスタは任意です(無くても読み取りはできます)。CSV(UTF-8 / Shift_JIS)・Excel に対応。</div>
      {isAdmin && (
        <div className="row gap">
          <button className="btn primary" disabled={busy} onClick={() => fileRef.current?.click()}>ファイルから取り込む</button>
          <Toggle checked={replace} onChange={setReplace} label="既存のマスタを置き換える" />
          <input ref={fileRef} type="file" hidden accept=".csv,.tsv,.txt,.xlsx,.xlsm" onChange={(e) => { const f = e.target.files?.[0]; if (f) importFile(f); e.target.value = ""; }} />
          <span className="spacer" />
          <button className="btn danger ghost" disabled={busy} onClick={() => {
            if (confirm("マスタをすべて削除しますか?")) run(() => api.del(`/api/masters/${kind}`).then(load), "削除しました");
          }}>すべて削除</button>
        </div>
      )}
      <Card title={`${data?.total ?? 0} 件`} actions={
        <form className="row gap" onSubmit={(e) => { e.preventDefault(); load(); }}>
          <input className="search" placeholder="検索" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn">検索</button>
        </form>
      }>
        {data && data.items.length === 0 ? <Empty>マスタは空です。</Empty> : (
          <div className="table-wrap">
            <table className="table compact">
              <thead>
                {kind === "products" ? (
                  <tr><th>商品コード</th><th>品名</th><th>単位</th><th className="num">単価</th></tr>
                ) : (
                  <tr><th>コード</th><th>会社名</th><th>部署</th><th>担当者</th><th>住所</th><th>〒</th><th>TEL</th></tr>
                )}
              </thead>
              <tbody>
                {data?.items.map((r) => kind === "products" ? (
                  <tr key={r.id}><td className="mono">{r.code}</td><td>{r.name}</td><td>{r.unit}</td><td className="num">{fmtNum(r.price)}</td></tr>
                ) : (
                  <tr key={r.id} className={r.hidden ? "muted" : ""}>
                    <td className="mono">{r.code}</td><td>{r.company}</td><td>{r.department}</td><td>{r.person}</td>
                    <td className="ellipsis">{r.address}</td><td>{r.zip}</td><td>{r.tel}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data && data.total > data.items.length && <p className="muted small">先頭 {data.items.length} 件を表示しています。</p>}
          </div>
        )}
      </Card>
    </div>
  );
}
