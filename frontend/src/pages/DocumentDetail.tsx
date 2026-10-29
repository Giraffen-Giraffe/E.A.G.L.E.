import { useEffect, useMemo, useState } from "react";
import { api, download } from "../api";
import PageViewer, { Box } from "../components/PageViewer";
import { Empty, Modal, Progress, StatusBadge, Tabs, Toggle, fmtDate, fmtNum, navigate, useAction, useInterval } from "../components/ui";
import type { DocDetail, FieldInfo, FieldsPayload, Info, Line, Template } from "../types";

const GROUPS: { title: string; keys: string[] }[] = [
  { title: "書類", keys: ["document_title", "order_number", "order_date", "delivery_date", "customer_order_number"] },
  { title: "発注元", keys: ["buyer_company", "buyer_department", "buyer_contact", "buyer_address", "buyer_tel", "buyer_fax", "buyer_code"] },
  { title: "納品先", keys: ["ship_to_company", "ship_to_department", "ship_to_contact", "ship_to_address", "ship_to_tel", "end_user", "ship_to_code"] },
  { title: "金額・条件", keys: ["subtotal", "tax", "total", "payment_terms", "notes"] },
];
const EXTRA_LABELS: Record<string, string> = { buyer_code: "発注元コード(顧客マスタ)", ship_to_code: "納品先コード(顧客マスタ)" };
const ROLE_LABEL: Record<string, string> = { orderer: "発注元", recipient: "宛先(受注側)", ship_to: "届け先", end_user: "最終利用者", other: "その他" };

type Tab = "header" | "lines" | "warnings" | "detail";

const same = (a: unknown, b: unknown) => String(a ?? "").replace(/\s/g, "") === String(b ?? "").replace(/\s/g, "");

export default function DocumentDetail({ id, info }: { id: number; info: Info }) {
  const [doc, setDoc] = useState<DocDetail | null>(null);
  const [header, setHeader] = useState<Record<string, unknown>>({});
  const [lines, setLines] = useState<Line[]>([]);
  const [dirty, setDirty] = useState(false);
  const [page, setPage] = useState(1);
  const [tab, setTab] = useState<Tab>("header");
  const [fields, setFields] = useState<FieldsPayload | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [showRoi, setShowRoi] = useState(true);
  const [reprocess, setReprocess] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const { busy, run } = useAction();
  const canEdit = info.user?.role !== "viewer";

  const apply = (d: DocDetail) => {
    setDoc(d);
    setHeader({ ...d.header });
    setLines(d.lines.map((l) => ({ ...l })));
    setDirty(false);
    if (!d.pages.some((p) => p.index === page)) setPage(d.pages[0]?.index ?? 1);
  };
  const load = () =>
    api.get<DocDetail>(`/api/documents/${id}`).then(apply).catch((e) => e.status === 404 && setNotFound(true));

  useEffect(() => {
    load();
    api.get<FieldsPayload>("/api/fields").then(setFields).catch(() => undefined);
    api.get<Template[]>("/api/templates").then(setTemplates).catch(() => undefined);
  }, [id]);

  const pending = doc?.status === "queued" || doc?.status === "processing";
  useInterval(() => { if (pending && !dirty) load(); }, pending ? 2000 : null);

  const fieldMap = useMemo(() => {
    const m: Record<string, FieldInfo> = {};
    fields?.header.forEach((f) => (m[f.key] = f));
    return m;
  }, [fields]);
  const customHeader = fields?.header.filter((f) => !f.builtin) ?? [];
  const customLine = fields?.line.filter((f) => !f.builtin) ?? [];

  const setH = (k: string, v: unknown) => { setHeader((h) => ({ ...h, [k]: v })); setDirty(true); };
  const setL = (i: number, k: keyof Line, v: unknown) => {
    setLines((ls) => ls.map((l, j) => (j === i ? { ...l, [k]: v } : l)));
    setDirty(true);
  };
  const setLExtra = (i: number, k: string, v: unknown) => {
    setLines((ls) => ls.map((l, j) => (j === i ? { ...l, extra: { ...l.extra, [k]: v } } : l)));
    setDirty(true);
  };
  const addLine = () => {
    setLines((ls) => [...ls, { line_no: ls.length + 1, page, order_number: "", product_code: "", raw_code: "", product_name: "", quantity: null, unit: "", unit_price: null, amount: null, due_date: "", notes: "", extra: {} }]);
    setDirty(true);
  };
  const removeLine = (i: number) => { setLines((ls) => ls.filter((_, j) => j !== i)); setDirty(true); };

  const save = (confirmIt = false) =>
    run(() => api.put<DocDetail>(`/api/documents/${id}`, { header, lines: lines as unknown as Record<string, unknown>[], confirm: confirmIt }).then(apply),
      confirmIt ? "確定しました(修正内容は学習に反映されます)" : "保存しました");

  const addRoi = async () => {
    if (!doc) return;
    sessionStorage.setItem("eagle.sampleDoc", String(doc.id));
    if (doc.template_id) return navigate(`#/templates/${doc.template_id}`);
    const t = await run(() => api.post<Template>(`/api/templates/from-document/${doc.id}`, {}));
    if (t) navigate(`#/templates/${t.id}`);
  };

  if (notFound) return <div className="page"><Empty>書類が見つかりません。<a className="link" href="#/documents">一覧へ戻る</a></Empty></div>;
  if (!doc) return <div className="page"><div className="muted">読み込み中…</div></div>;

  const roiBoxes: Box[] = showRoi
    ? doc.roi.filter((r) => r.page === page && r.box?.length === 4).map((r, i) => ({
        id: `${r.roi_id}-${i}`, x: r.box[0], y: r.box[1], w: r.box[2], h: r.box[3], label: r.label, tone: "result" as const,
      }))
    : [];
  const warnings = doc.warnings.filter((w) => w.startsWith("要確認"));
  const notes = doc.warnings.filter((w) => !w.startsWith("要確認"));
  const sum = lines.reduce((a, l) => a + (Number(l.amount) || 0), 0);
  const label = (k: string) => EXTRA_LABELS[k] ?? fieldMap[k]?.label ?? k;

  const renderField = (k: string) => {
    const ext = doc.extracted_header?.[k];
    const changed = doc.status !== "queued" && k in (doc.extracted_header || {}) && !same(ext, header[k]);
    const match = k === "buyer_code" ? doc.buyer_match : k === "ship_to_code" ? doc.ship_to_match : null;
    const long = ["notes", "buyer_address", "ship_to_address", "payment_terms"].includes(k);
    return (
      <label key={k} className={`hf ${long ? "wide" : ""} ${changed ? "changed" : ""}`}>
        <span className="hf-label">
          {label(k)}
          {changed && <span className="tag tag-changed" title={`読取値: ${String(ext ?? "")}`}>修正</span>}
        </span>
        {long ? (
          <textarea rows={2} value={String(header[k] ?? "")} disabled={!canEdit} onChange={(e) => setH(k, e.target.value)} />
        ) : (
          <input value={String(header[k] ?? "")} disabled={!canEdit} onChange={(e) => setH(k, e.target.value)}
            type={fieldMap[k]?.kind === "date" ? "text" : "text"} placeholder={fieldMap[k]?.kind === "date" ? "YYYY-MM-DD" : ""} />
        )}
        {match && <span className="hf-hint" title={`根拠: ${match.why}(点数 ${match.score})`}>✓ マスタ: {match.label}</span>}
      </label>
    );
  };

  return (
    <div className="page detail">
      <header className="page-head">
        <div className="row gap">
          <button className="btn ghost" onClick={() => history.length > 1 ? history.back() : navigate("#/documents")}>← 戻る</button>
          <h1 className="ellipsis" title={doc.filename}>{doc.filename}</h1>
          <StatusBadge status={doc.status} />
          {doc.template && <span className="tag">テンプレート: {doc.template}</span>}
        </div>
        <div className="row gap">
          <button className="btn ghost" onClick={() => download(`/api/documents/${id}/original`)}>元ファイル</button>
          {canEdit && (
            <>
              <button className="btn" onClick={addRoi} disabled={busy || pending}>ROI を追加・編集</button>
              <button className="btn" onClick={() => setReprocess(true)} disabled={busy || pending}>再処理</button>
              {doc.status === "confirmed" ? (
                <button className="btn" onClick={() => run(() => api.post<DocDetail>(`/api/documents/${id}/unconfirm`).then(apply), "確定を解除しました")}>確定解除</button>
              ) : null}
              <button className="btn" onClick={() => save(false)} disabled={busy || !dirty || pending}>保存</button>
              <button className="btn primary" onClick={() => save(true)} disabled={busy || pending}>
                {doc.status === "confirmed" ? "保存(確定済)" : "確定"}
              </button>
            </>
          )}
        </div>
      </header>

      {pending && (
        <div className="notice">
          {doc.status === "queued" ? "処理待ちです。" : "読み取り中です。"}
          {doc.job && <Progress value={doc.job.progress} label={doc.job.message} />}
        </div>
      )}
      {doc.status === "failed" && <div className="notice bad"><b>処理に失敗しました。</b><pre className="pre">{doc.status_detail}</pre></div>}
      {doc.status === "skipped" && (
        <div className="notice">{doc.status_detail} ── 注文書であれば「再処理」で「対象外判定を無視」を選んでください。</div>
      )}

      <div className="split">
        <div className="split-left">
          <PageViewer
            docId={doc.id}
            pages={doc.pages}
            page={page}
            onPage={setPage}
            boxes={roiBoxes}
            toolbar={doc.roi.length > 0 && <Toggle checked={showRoi} onChange={setShowRoi} label="ROI" />}
          />
        </div>
        <div className="split-right">
          <Tabs<Tab>
            value={tab}
            onChange={setTab}
            tabs={[
              { key: "header", label: "基本情報" },
              { key: "lines", label: `明細 (${lines.length})` },
              { key: "warnings", label: <>確認事項 {warnings.length > 0 && <span className="count bad">{warnings.length}</span>}</> },
              { key: "detail", label: "読取詳細" },
            ]}
          />
          {tab === "header" && (
            <div className="hform">
              {GROUPS.map((g) => (
                <fieldset key={g.title}>
                  <legend>{g.title}</legend>
                  <div className="hgrid">{g.keys.map(renderField)}</div>
                </fieldset>
              ))}
              {customHeader.length > 0 && (
                <fieldset>
                  <legend>追加項目</legend>
                  <div className="hgrid">{customHeader.map((f) => renderField(f.key))}</div>
                </fieldset>
              )}
            </div>
          )}
          {tab === "lines" && (
            <div className="lines">
              <div className="table-wrap">
                <table className="table edit">
                  <thead>
                    <tr>
                      <th>#</th><th>注文番号</th><th>商品コード</th><th>品名</th><th className="num">数量</th><th>単位</th>
                      <th className="num">単価</th><th className="num">金額</th><th>納期</th><th>備考</th>
                      {customLine.map((f) => <th key={f.key}>{f.label}</th>)}
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((l, i) => {
                      const codeChanged = l.extracted && Object.keys(l.extracted).length > 0 && !same(l.extracted.product_code, l.product_code);
                      return (
                        <tr key={l.id ?? `n${i}`}>
                          <td className="muted">{i + 1}</td>
                          <td><input className="w-ord" value={l.order_number} disabled={!canEdit} onChange={(e) => setL(i, "order_number", e.target.value)} /></td>
                          <td>
                            <div className={`code-cell ${l.master_matched ? "ok" : "ng"} ${codeChanged ? "changed" : ""}`}>
                              <input className="w-code mono" value={l.product_code} disabled={!canEdit} onChange={(e) => setL(i, "product_code", e.target.value)}
                                title={`読取: ${l.raw_code}${l.match_method ? ` / 照合: ${l.match_method}` : ""}`} />
                            </div>
                          </td>
                          <td><input className="w-name" value={l.product_name} disabled={!canEdit} onChange={(e) => setL(i, "product_name", e.target.value)} /></td>
                          <td><input className="w-num num" value={l.quantity ?? ""} disabled={!canEdit} onChange={(e) => setL(i, "quantity", e.target.value)} /></td>
                          <td><input className="w-unit" value={l.unit} disabled={!canEdit} onChange={(e) => setL(i, "unit", e.target.value)} /></td>
                          <td><input className="w-num num" value={l.unit_price ?? ""} disabled={!canEdit} onChange={(e) => setL(i, "unit_price", e.target.value)} /></td>
                          <td><input className="w-num num" value={l.amount ?? ""} disabled={!canEdit} onChange={(e) => setL(i, "amount", e.target.value)} /></td>
                          <td><input className="w-date" value={l.due_date} disabled={!canEdit} onChange={(e) => setL(i, "due_date", e.target.value)} /></td>
                          <td><input className="w-note" value={l.notes} disabled={!canEdit} onChange={(e) => setL(i, "notes", e.target.value)} /></td>
                          {customLine.map((f) => (
                            <td key={f.key}><input className="w-note" value={String(l.extra?.[f.key] ?? "")} disabled={!canEdit} onChange={(e) => setLExtra(i, f.key, e.target.value)} /></td>
                          ))}
                          <td>{canEdit && <button className="icon-btn" title="行を削除" onClick={() => removeLine(i)}>×</button>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td colSpan={7} className="right muted">明細合計</td>
                      <td className="num strong">{fmtNum(sum)}</td>
                      <td colSpan={3 + customLine.length} />
                    </tr>
                  </tfoot>
                </table>
              </div>
              {canEdit && <button className="btn small" onClick={addLine}>＋ 行を追加</button>}
              <p className="muted small">緑の枠=商品マスタで確定したコード / 橙の枠=マスタに無いコード。コードを直して確定すると、次回から同じ読みを自動で直します。</p>
            </div>
          )}
          {tab === "warnings" && (
            <div className="warnings">
              {warnings.length === 0 && notes.length === 0 && <Empty>確認事項はありません。</Empty>}
              {warnings.map((w, i) => <div key={i} className="warn-item">{w}</div>)}
              {notes.map((w, i) => <div key={`n${i}`} className="note-item">{w}</div>)}
            </div>
          )}
          {tab === "detail" && (
            <div className="detail-info">
              <dl className="kv">
                <dt>テンプレート判定</dt><dd>{doc.template ? `${doc.template}(${doc.template_match})` : "なし(汎用の読取)"}</dd>
                <dt>モデル</dt><dd>{doc.model_name}</dd>
                <dt>処理時間</dt><dd>{doc.processing_sec} 秒(VLM 呼び出し {doc.vlm_calls} 回)</dd>
                <dt>内訳</dt><dd>{Object.entries(doc.timings).map(([k, v]) => `${k}: ${v}s`).join(" / ")}</dd>
                <dt>取込</dt><dd>{fmtDate(doc.created_at)}({doc.source})</dd>
                {doc.confirmed_at && (<><dt>確定</dt><dd>{fmtDate(doc.confirmed_at)} {doc.confirmed_by}</dd></>)}
              </dl>
              <h4>読み取った会社</h4>
              <table className="table compact">
                <tbody>
                  {doc.companies.map((c, i) => (
                    <tr key={i}>
                      <td><span className="tag">{ROLE_LABEL[c.role] ?? c.role}</span></td>
                      <td>{c.name} {c.department}</td>
                      <td className="muted">{c.contact}</td>
                      <td className="muted small">{c.address} {c.tel}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {canEdit && (
                <div className="danger-zone">
                  <button className="btn danger ghost small" onClick={() => {
                    if (confirm("この書類を削除します。よろしいですか?")) run(() => api.del(`/api/documents/${id}`).then(() => navigate("#/documents")), "削除しました");
                  }}>この書類を削除</button>
                </div>
              )}
              {doc.roi.length > 0 && (
                <>
                  <h4>ROI 焦点読取の結果</h4>
                  <table className="table compact">
                    <tbody>
                      {doc.roi.map((r, i) => (
                        <tr key={i}>
                          <td>{r.label}</td>
                          <td className="muted">p{r.page} / {r.source}</td>
                          <td className="small">{typeof r.value === "string" ? r.value : JSON.stringify(r.value)}</td>
                          <td className="muted small">{r.note}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {reprocess && (
        <ReprocessDialog doc={doc} templates={templates} onClose={() => setReprocess(false)}
          onDone={() => { setReprocess(false); load(); }} />
      )}
    </div>
  );
}

function ReprocessDialog({ doc, templates, onClose, onDone }: {
  doc: DocDetail; templates: Template[]; onClose: () => void; onDone: () => void;
}) {
  const [tid, setTid] = useState<string>(doc.template_locked && doc.template_id ? String(doc.template_id) : "");
  const [force, setForce] = useState(doc.status === "skipped");
  const { busy, run } = useAction();
  return (
    <Modal title="再処理" onClose={onClose}
      footer={<>
        <button className="btn ghost" onClick={onClose}>取消</button>
        <button className="btn primary" disabled={busy}
          onClick={() => run(() => api.post(`/api/documents/${doc.id}/reprocess`, { template_id: tid ? Number(tid) : null, force }).then(onDone), "再処理を依頼しました")}>
          再処理する
        </button>
      </>}>
      <p className="muted">現在の設定・テンプレート(ROI)・学習内容で読み直します。手で修正した内容は上書きされます。</p>
      <label className="field">
        <span className="field-label">テンプレート</span>
        <select value={tid} onChange={(e) => setTid(e.target.value)}>
          <option value="">自動で判定する</option>
          {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </label>
      <Toggle checked={force} onChange={setForce} label="注文書以外(対象外)の判定を無視して読む" />
    </Modal>
  );
}
