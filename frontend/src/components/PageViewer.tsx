import { PointerEvent as RPointerEvent, ReactNode, useEffect, useRef, useState } from "react";
import type { PageMeta } from "../types";

export interface Box {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  label?: string;
  tone?: "roi" | "result" | "muted";
  selected?: boolean;
}

type Drag =
  | { kind: "new"; x0: number; y0: number; x: number; y: number }
  | { kind: "move"; id: string; dx: number; dy: number; box: Box }
  | { kind: "resize"; id: string; box: Box };

const clamp = (v: number) => Math.max(0, Math.min(1, v));

export default function PageViewer({
  docId, pages, page, onPage, boxes = [], editable = false, onCreate, onChange, onSelect, toolbar,
}: {
  docId: number;
  pages: PageMeta[];
  page: number;
  onPage: (p: number) => void;
  boxes?: Box[];
  editable?: boolean;
  onCreate?: (b: { x: number; y: number; w: number; h: number }) => void;
  onChange?: (id: string, b: { x: number; y: number; w: number; h: number }) => void;
  onSelect?: (id: string | null) => void;
  toolbar?: ReactNode;
}) {
  const [zoom, setZoom] = useState(1);
  const [drag, setDrag] = useState<Drag | null>(null);
  const inner = useRef<HTMLDivElement>(null);
  const meta = pages.find((p) => p.index === page);

  useEffect(() => setDrag(null), [page]);

  const rel = (e: RPointerEvent) => {
    const r = inner.current!.getBoundingClientRect();
    return { x: clamp((e.clientX - r.left) / r.width), y: clamp((e.clientY - r.top) / r.height) };
  };

  const onDown = (e: RPointerEvent<HTMLDivElement>) => {
    if (!editable || e.button !== 0) return;
    const target = e.target as HTMLElement;
    const id = target.closest<HTMLElement>("[data-box]")?.dataset.box;
    const p = rel(e);
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    if (id) {
      const b = boxes.find((x) => x.id === id)!;
      onSelect?.(id);
      if (target.dataset.handle) setDrag({ kind: "resize", id, box: b });
      else setDrag({ kind: "move", id, dx: p.x - b.x, dy: p.y - b.y, box: b });
    } else {
      onSelect?.(null);
      setDrag({ kind: "new", x0: p.x, y0: p.y, x: p.x, y: p.y });
    }
  };

  const onMove = (e: RPointerEvent<HTMLDivElement>) => {
    if (!drag) return;
    const p = rel(e);
    if (drag.kind === "new") setDrag({ ...drag, x: p.x, y: p.y });
    else if (drag.kind === "move") {
      const b = drag.box;
      onChange?.(drag.id, { x: clamp(Math.min(1 - b.w, p.x - drag.dx)), y: clamp(Math.min(1 - b.h, p.y - drag.dy)), w: b.w, h: b.h });
    } else {
      const b = drag.box;
      onChange?.(drag.id, { x: b.x, y: b.y, w: Math.max(0.005, p.x - b.x), h: Math.max(0.005, p.y - b.y) });
    }
  };

  const onUp = () => {
    if (drag?.kind === "new") {
      const x = Math.min(drag.x0, drag.x), y = Math.min(drag.y0, drag.y);
      const w = Math.abs(drag.x - drag.x0), h = Math.abs(drag.y - drag.y0);
      if (w > 0.006 && h > 0.004) onCreate?.({ x, y, w, h });
    }
    setDrag(null);
  };

  const temp = drag?.kind === "new"
    ? { x: Math.min(drag.x0, drag.x), y: Math.min(drag.y0, drag.y), w: Math.abs(drag.x - drag.x0), h: Math.abs(drag.y - drag.y0) }
    : null;

  return (
    <div className="viewer">
      <div className="viewer-bar">
        <div className="pager">
          {pages.map((p) => (
            <button key={p.index} className={`chip ${p.index === page ? "active" : ""}`} onClick={() => onPage(p.index)}>
              {p.index}
            </button>
          ))}
        </div>
        <div className="viewer-tools">
          {toolbar}
          <button className="icon-btn" title="縮小" onClick={() => setZoom((z) => Math.max(0.4, +(z - 0.2).toFixed(1)))}>−</button>
          <span className="zoom">{Math.round(zoom * 100)}%</span>
          <button className="icon-btn" title="拡大" onClick={() => setZoom((z) => Math.min(4, +(z + 0.2).toFixed(1)))}>＋</button>
          <button className="btn small ghost" onClick={() => setZoom(1)}>幅に合わせる</button>
        </div>
      </div>
      <div className="viewer-scroll">
        {meta?.kind === "image" ? (
          <div
            ref={inner}
            className={`viewer-page ${editable ? "editable" : ""}`}
            style={{ width: `${zoom * 100}%` }}
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={onUp}
          >
            <img src={`/api/documents/${docId}/pages/${page}`} alt={`${page}ページ`} draggable={false} />
            {boxes.map((b) => (
              <div
                key={b.id}
                data-box={b.id}
                className={`box tone-${b.tone ?? "roi"} ${b.selected ? "selected" : ""}`}
                style={{ left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` }}
              >
                {b.label && <span className="box-label">{b.label}</span>}
                {editable && b.selected && <span className="box-handle" data-handle="1" data-box={b.id} />}
              </div>
            ))}
            {temp && (
              <div className="box tone-roi drawing" style={{ left: `${temp.x * 100}%`, top: `${temp.y * 100}%`, width: `${temp.w * 100}%`, height: `${temp.h * 100}%` }} />
            )}
          </div>
        ) : (
          <div className="viewer-text">このページはテキスト文書(メール本文・Excel 等)です。画像はありません。</div>
        )}
      </div>
    </div>
  );
}
