import { useEffect, useState } from "react";
import { api } from "../api";
import { Card, Empty, fmtDate, navigate } from "../components/ui";
import type { Template } from "../types";

export default function Templates() {
  const [items, setItems] = useState<(Template & { updated_at?: string })[] | null>(null);
  useEffect(() => { api.get<Template[]>("/api/templates").then(setItems).catch(() => setItems([])); }, []);

  return (
    <div className="page">
      <header className="page-head">
        <h1>テンプレート・ROI</h1>
        <button className="btn primary" onClick={() => navigate("#/templates/new")}>＋ 新規テンプレート</button>
      </header>
      <div className="notice">
        テンプレートは「発行元ごとの読み方」です。テンプレートが無くても汎用の読取で動きますが、
        読み違いが多い発行元は <b>ROI(読取領域)</b> を登録すると、その欄だけを切り出して精読するため精度が上がります。
        書類の詳細画面の「ROI を追加・編集」からも作れます。
      </div>
      <Card>
        {items && items.length === 0 ? (
          <Empty>テンプレートはまだありません。</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr><th>名前</th><th>判定キーワード</th><th className="num">ROI</th><th className="num">書類</th><th>状態</th><th>更新</th></tr>
            </thead>
            <tbody>
              {items?.map((t) => (
                <tr key={t.id} className="clickable" onClick={() => navigate(`#/templates/${t.id}`)}>
                  <td className="strong">{t.name}</td>
                  <td className="ellipsis">{t.match_keywords.join(" / ")}</td>
                  <td className="num">{t.rois.length}</td>
                  <td className="num">{t.documents ?? 0}</td>
                  <td>{t.enabled ? <span className="tag ok">有効</span> : <span className="tag">無効</span>}</td>
                  <td className="muted">{fmtDate(t.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
