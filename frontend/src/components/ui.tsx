import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { Status } from "../types";

// ── 書式 ──────────────────────────────────────────────────────────────
export const STATUS_LABEL: Record<Status, string> = {
  queued: "待機中",
  processing: "処理中",
  extracted: "抽出済",
  needs_review: "要確認",
  confirmed: "確定",
  skipped: "対象外",
  failed: "失敗",
};

export function fmtNum(v: unknown, digits = 0): string {
  if (v === null || v === undefined || v === "") return "";
  const n = typeof v === "number" ? v : Number(String(v).replace(/,/g, ""));
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString("ja-JP", { maximumFractionDigits: digits || (Number.isInteger(n) ? 0 : 2) });
}

export function fmtDate(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  const date = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  return withTime ? `${date} ${p(d.getHours())}:${p(d.getMinutes())}` : date;
}

export function fmtBytes(n: number): string {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " GB";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + " MB";
  return Math.round(n / 1e3) + " KB";
}

// ── 部品 ──────────────────────────────────────────────────────────────
export function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge st-${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export function Card({ title, actions, children, className = "" }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <h2>{title}</h2>
          <div className="card-actions">{actions}</div>
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}

export function Toggle({ checked, onChange, label, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; label?: ReactNode; disabled?: boolean;
}) {
  return (
    <label className={`toggle ${disabled ? "disabled" : ""}`}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-track"><span className="toggle-thumb" /></span>
      {label && <span className="toggle-label">{label}</span>}
    </label>
  );
}

export function Field({ label, hint, children, wide }: { label: ReactNode; hint?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <label className={`field ${wide ? "wide" : ""}`}>
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

/** 文字列配列を1行1件のテキストで編集する。 */
export function LinesInput({ value, onChange, rows = 4, placeholder }: {
  value: string[]; onChange: (v: string[]) => void; rows?: number; placeholder?: string;
}) {
  const [text, setText] = useState((value || []).join("\n"));
  useEffect(() => setText((value || []).join("\n")), [JSON.stringify(value)]);
  return (
    <textarea
      rows={rows}
      value={text}
      placeholder={placeholder}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => onChange(text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean))}
    />
  );
}

/** {キー: 値} を "キー=値" の行で編集する。 */
export function KeyValueInput({ value, onChange, rows = 3, placeholder }: {
  value: Record<string, string>; onChange: (v: Record<string, string>) => void; rows?: number; placeholder?: string;
}) {
  const toText = (v: Record<string, string>) => Object.entries(v || {}).map(([k, x]) => `${k}=${x}`).join("\n");
  const [text, setText] = useState(toText(value));
  useEffect(() => setText(toText(value)), [JSON.stringify(value)]);
  return (
    <textarea
      rows={rows}
      value={text}
      placeholder={placeholder}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        const out: Record<string, string> = {};
        text.split(/\r?\n/).forEach((ln) => {
          const i = ln.indexOf("=");
          if (i > 0) out[ln.slice(0, i).trim()] = ln.slice(i + 1).trim();
        });
        onChange(out);
      }}
    />
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: {
  tabs: { key: T; label: ReactNode }[]; value: T; onChange: (k: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.key} role="tab" className={`tab ${value === t.key ? "active" : ""}`} onClick={() => onChange(t.key)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ title, onClose, children, footer, width = 560 }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; width?: number;
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ width }}>
        <header className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="閉じる">×</button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  );
}

export function Progress({ value, label }: { value: number; label?: ReactNode }) {
  return (
    <div className="progress">
      <div className="progress-bar" style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
      {label && <span className="progress-label">{label}</span>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

// ── 通知 ──────────────────────────────────────────────────────────────
type ToastKind = "info" | "ok" | "error";
interface ToastItem { id: number; kind: ToastKind; text: string }
const ToastCtx = createContext<(text: string, kind?: ToastKind) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const push = useCallback((text: string, kind: ToastKind = "info") => {
    const id = ++seq.current;
    setItems((xs) => [...xs, { id, kind, text }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), kind === "error" ? 8000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`} onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

// ── フック ────────────────────────────────────────────────────────────
export function useInterval(fn: () => void, ms: number | null) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (ms === null) return;
    const id = setInterval(() => ref.current(), ms);
    return () => clearInterval(id);
  }, [ms]);
}

/** 非同期処理を実行し、失敗時は通知する。 */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(async <T,>(fn: () => Promise<T>, okText?: string): Promise<T | undefined> => {
    setBusy(true);
    try {
      const r = await fn();
      if (okText) toast(okText, "ok");
      return r;
    } catch (e) {
      toast((e as Error).message || String(e), "error");
      return undefined;
    } finally {
      setBusy(false);
    }
  }, [toast]);
  return { busy, run };
}

export function navigate(hash: string) {
  window.location.hash = hash;
}
