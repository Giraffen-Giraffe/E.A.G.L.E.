import { useEffect, useMemo, useState } from "react";
import { api, qs } from "../api";
import PageViewer, { Box } from "../components/PageViewer";
import { Field, KeyValueInput, LinesInput, Toggle, navigate, useAction } from "../components/ui";
import type { DocDetail, DocSummary, FieldsPayload, Roi, RoiResult, Template } from "../types";

const EMPTY: Template = {
  id: 0, name: "", description: "", enabled: true, priority: 0, match_keywords: [], anchor: "content", reference: {},
  sample_document_id: null, rois: [], prompt_hint: "", defaults: {}, overrides: {}, patterns: {},
};

let seq = 0;
const newId = () => `roi${Date.now().toString(36)}${(seq++).toString(36)}`;

export default function TemplateEditor({ id }: { id: number | null }) {
  const [t, setT] = useState<Template>(EMPTY);
  const [fields, setFields] = useState<FieldsPayload | null>(null);
  const [docs, setDocs] = useState<DocSummary[]>([]);
  const [sample, setSample] = useState<DocDetail | null>(null);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, RoiResult[]>>({});
  const [dirty, setDirty] = useState(false);
  const { busy, run } = useAction();

  useEffect(() => {
    api.get<FieldsPayload>("/api/fields").then(setFields).catch(() => undefined);
    api.get<{ items: DocSummary[] }>("/api/documents" + qs({ page_size: 100, status: "extracted,needs_review,confirmed" }))
      .then((r) => setDocs(r.items)).catch(() => undefined);
    if (id) {
      api.get<Template>(`/api/templates/${id}`).then((x) => {
        setT(x);
        const fromDoc = Number(sessionStorage.getItem("eagle.sampleDoc") || 0);
        sessionStorage.removeItem("eagle.sampleDoc");
        loadSample(fromDoc || x.sample_document_id);
        if (fromDoc && fromDoc !== x.sample_document_id) {
          setT({ ...x, sample_document_id: fromDoc, reference: {} });
          setDirty(true);
        }
      });
    } else {
      const fromDoc = Number(sessionStorage.getItem("eagle.sampleDoc") || 0);
      sessionStorage.removeItem("eagle.sampleDoc");
      if (fromDoc) { setT({ ...EMPTY, sample_document_id: fromDoc }); loadSample(fromDoc); }
    }
  }, [id]);

  const loadSample = (docId: number | null) => {
    if (!docId) { setSample(null); return; }
    api.get<DocDetail>(`/api/documents/${docId}`).then((d) => { setSample(d); setPage(d.pages[0]?.index ?? 1); }).catch(() => setSample(null));
  };

  const update = (patch: Partial<Template>) => { setT((x) => ({ ...x, ...patch })); setDirty(true); };
  const updateRoi = (rid: string, patch: Partial<Roi>) =>
    update({ rois: t.rois.map((r) => (r.id === rid ? { ...r, ...patch } : r)) });

  const targetOptions = useMemo(() => {
    const opts: { key: string; label: string; group: string }[] = [];
    fields?.roi_blocks.forEach((b) => opts.push({ key: b.key, label: b.label, group: "ブロック" }));
    fields?.header.forEach((f) => opts.push({ key: f.key, label: f.label, group: f.builtin ? "項目" : "追加項目" }));
    return opts;
  }, [fields]);
  const targetLabel = (k: string) => targetOptions.find((o) => o.key === k)?.label ?? k;

  const boxes: Box[] = t.rois
    .filter((r) => r.page === page || (r.field === "@line_items" && r.page === 0))
    .map((r) => ({ id: r.id, x: r.x, y: r.y, w: r.w, h: r.h, label: r.label || targetLabel(r.field), selected: r.id === selected, tone: r.enabled ? "roi" : "muted" }));

  const onCreate = (b: { x: number; y: number; w: number; h: number }) => {
    const roi: Roi = { id: newId(), label: "", field: "@buyer", page, ...b, mode: "auto", merge: "override", instruction: "", regex: "", pad: 0.006, enabled: true };
    update({ rois: [...t.rois, roi] });
    setSelected(roi.id);
  };

  const save = async () => {
    const body = { ...t } as unknown as Record<string, unknown>;
    const saved = await run(() => (t.id ? api.put<Template>(`/api/templates/${t.id}`, body) : api.post<Template>("/api/templates", body)), "保存しました");
    if (saved) {
      setT(saved);
      setDirty(false);
      if (!t.id) navigate(`#/templates/${saved.id}`);
    }
  };

  const test = async (only?: string) => {
    if (!sample) return;
    const res = await run(() => api.post<{ results: RoiResult[] }>("/api/templates/test", {
      document_id: sample.id, template: t as unknown as Record<string, unknown>, roi_ids: only ? [only] : null,
    }));
    if (res) {
      const by: Record<string, RoiResult[]> = only ? { ...results } : {};
      res.results.forEach((r) => (by[r.roi_id] = [...(only ? [] : by[r.roi_id] ?? []), r]));
      setResults(by);
    }
  };

  const reprocessSample = () => sample && run(() => api.post(`/api/documents/${sample.id}/reprocess`, { template_id: t.id }), "見本の書類をこのテンプレートで再処理します");

  const sel = t.rois.find((r) => r.id === selected) ?? null;

  return (
    <div className="page detail">
      <header className="page-head">
        <div className="row gap">
          <button className="btn ghost" onClick={() => navigate("#/templates")}>← 一覧</button>
          <h1>{t.id ? t.name || "(無名)" : "新規テンプレート"}</h1>
          {dirty && <span className="tag tag-changed">未保存</span>}
        </div>
        <div className="row gap">
          {t.id > 0 && (
            <button className="btn danger ghost" onClick={() => {
              if (confirm("このテンプレートを削除しますか?")) run(() => api.del(`/api/templates/${t.id}`).then(() => navigate("#/templates")), "削除しました");
            }}>削除</button>
          )}
          {sample && t.rois.length > 0 && <button className="btn" disabled={busy} onClick={() => test()}>全 ROI を試し読み</button>}
          {sample && t.id > 0 && <button className="btn" disabled={busy || dirty} onClick={reprocessSample} title="保存後に実行できます">見本を再処理</button>}
          <button className="btn primary" disabled={busy || !t.name.trim()} onClick={save}>保存</button>
        </div>
      </header>

      <div className="split">
        <div className="split-left">
          <div className="row gap sample-pick">
            <span className="muted">見本の書類:</span>
            <select value={t.sample_document_id ?? ""} onChange={(e) => { const v = Number(e.target.value) || null; update({ sample_document_id: v, reference: {} }); loadSample(v); }}>
              <option value="">(選択してください)</option>
              {sample && !docs.some((d) => d.id === sample.id) && <option value={sample.id}>#{sample.id} {sample.filename}</option>}
              {docs.map((d) => <option key={d.id} value={d.id}>#{d.id} {d.buyer_company || d.filename}</option>)}
            </select>
          </div>
          {sample ? (
            <>
              <div className="hint-bar">画像上をドラッグして読取領域(ROI)を追加 / 枠をクリックで選択・ドラッグで移動・右下の角でサイズ変更</div>
              <PageViewer docId={sample.id} pages={sample.pages} page={page} onPage={setPage} boxes={boxes} editable
                onCreate={onCreate} onSelect={setSelected} onChange={(rid, b) => updateRoi(rid, b)} />
            </>
          ) : (
            <div className="empty">見本の書類を選ぶと、画像の上で ROI を描けます。</div>
          )}
        </div>

        <div className="split-right">
          <div className="form">
            <Field label="テンプレート名"><input value={t.name} onChange={(e) => update({ name: e.target.value })} /></Field>
            <Field label="判定キーワード(1行に1つ)" hint="紙面の社名・本文・ファイル名にこの語があればこのテンプレートを使います。長い語は1文字の誤読まで許容。re: で始めると正規表現。">
              <LinesInput value={t.match_keywords} onChange={(v) => update({ match_keywords: v })} rows={3} placeholder="例: ○○商事" />
            </Field>
            <div className="row gap wrap">
              <Toggle checked={t.enabled} onChange={(v) => update({ enabled: v })} label="有効" />
              <Toggle checked={t.anchor === "content"} onChange={(v) => update({ anchor: v ? "content" : "page" })} label="スキャン位置のズレを自動補正(印字範囲を基準にする)" />
            </div>
            <Field label="読取ヒント(この発行元の癖をVLMに伝える)" hint="例: 発注番号は品名の右横に行ごとに書かれている / 『納入場所』欄が届け先">
              <textarea rows={3} value={t.prompt_hint} onChange={(e) => update({ prompt_hint: e.target.value })} />
            </Field>
          </div>

          <h3 className="section-title">ROI(読取領域) {t.rois.length}件</h3>
          {t.rois.length === 0 && <p className="muted">左の画像上をドラッグして追加してください。</p>}
          <div className="roi-list">
            {t.rois.map((r) => (
              <div key={r.id} className={`roi-item ${r.id === selected ? "active" : ""}`} onClick={() => { setSelected(r.id); if (r.page) setPage(r.page); }}>
                <div className="row between">
                  <b>{r.label || targetLabel(r.field)}</b>
                  <span className="muted small">p{r.page || "全"} / {r.field}</span>
                </div>
                {(results[r.id] ?? []).map((res, i) => (
                  <div key={i} className="roi-result">
                    → {typeof res.value === "string" ? res.value || "(空)" : JSON.stringify(res.value)}
                    <span className="muted small"> [{res.source}{res.note ? ` / ${res.note}` : ""}]</span>
                  </div>
                ))}
              </div>
            ))}
          </div>

          {sel && (
            <div className="roi-edit">
              <h4>選択中の ROI</h4>
              <Field label="書き込み先">
                <select value={sel.field} onChange={(e) => updateRoi(sel.id, { field: e.target.value, page: e.target.value === "@line_items" ? sel.page : sel.page || page })}>
                  {["ブロック", "項目", "追加項目"].map((g) => (
                    <optgroup key={g} label={g}>
                      {targetOptions.filter((o) => o.group === g).map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                    </optgroup>
                  ))}
                </select>
              </Field>
              <div className="row gap wrap">
                <Field label="表示名"><input value={sel.label} onChange={(e) => updateRoi(sel.id, { label: e.target.value })} placeholder={targetLabel(sel.field)} /></Field>
                <Field label="ページ" hint={sel.field === "@line_items" ? "0 = 全ページ" : undefined}>
                  <input type="number" min={0} value={sel.page} onChange={(e) => updateRoi(sel.id, { page: Number(e.target.value) })} style={{ width: 80 }} />
                </Field>
              </div>
              <div className="row gap wrap">
                <Field label="読み方">
                  <select value={sel.mode} onChange={(e) => updateRoi(sel.id, { mode: e.target.value as Roi["mode"] })}>
                    <option value="auto">自動(電子PDFは文字情報、画像はVLM)</option>
                    <option value="vlm">常に VLM で読む</option>
                    <option value="text">文字情報のみ(電子PDF)</option>
                  </select>
                </Field>
                <Field label="反映">
                  <select value={sel.merge} onChange={(e) => updateRoi(sel.id, { merge: e.target.value as Roi["merge"] })}>
                    <option value="override">読めたら上書き</option>
                    <option value="fallback">全体読取で空の時だけ</option>
                  </select>
                </Field>
              </div>
              <Field label="VLM への補足指示" hint="例: この欄には発注担当者の氏名だけが書かれている">
                <input value={sel.instruction} onChange={(e) => updateRoi(sel.id, { instruction: e.target.value })} />
              </Field>
              <Field label="値の書式(正規表現・任意)" hint="合わない読みは捨てます。例: [A-Z]{2}\d{6}">
                <input className="mono" value={sel.regex} onChange={(e) => updateRoi(sel.id, { regex: e.target.value })} />
              </Field>
              <div className="row gap wrap">
                <Toggle checked={sel.enabled} onChange={(v) => updateRoi(sel.id, { enabled: v })} label="有効" />
                {sample && <button className="btn small" disabled={busy} onClick={() => test(sel.id)}>この ROI を試し読み</button>}
                <button className="btn small danger ghost" onClick={() => { update({ rois: t.rois.filter((r) => r.id !== sel.id) }); setSelected(null); }}>削除</button>
              </div>
            </div>
          )}

          <details className="advanced">
            <summary>詳細設定(書式・既定値・固定値)</summary>
            <Field label="項目の書式(項目キー=正規表現)" hint="書式に合わない読みは、本文や他の欄から書式に合う値を探して補正します。例: order_number=[A-Z]{2}\d{4}-\d{6}">
              <KeyValueInput value={t.patterns} onChange={(v) => update({ patterns: v })} />
            </Field>
            <Field label="既定値(空欄の時に補う。項目キー=値)" hint="例: ship_to_company=○○物流センター">
              <KeyValueInput value={t.defaults} onChange={(v) => update({ defaults: v })} />
            </Field>
            <Field label="固定値(常に上書き。項目キー=値)" hint="例: buyer_code=1001(この発行元は常に同じ顧客コード)">
              <KeyValueInput value={t.overrides} onChange={(v) => update({ overrides: v })} />
            </Field>
            <Field label="優先度"><input type="number" value={t.priority} onChange={(e) => update({ priority: Number(e.target.value) })} style={{ width: 100 }} /></Field>
            <Field label="メモ"><textarea rows={2} value={t.description} onChange={(e) => update({ description: e.target.value })} /></Field>
            <p className="muted small">項目キー: {fields?.header.map((f) => `${f.key}(${f.label})`).join(" / ")} / buyer_code / ship_to_code</p>
          </details>
        </div>
      </div>
    </div>
  );
}
