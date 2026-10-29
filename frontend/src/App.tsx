import { DragEvent, useCallback, useEffect, useState } from "react";
import { api, uploadFiles } from "./api";
import { useToast } from "./components/ui";
import type { Info } from "./types";
import Home from "./pages/Home";
import Documents from "./pages/Documents";
import DocumentDetail from "./pages/DocumentDetail";
import Templates from "./pages/Templates";
import TemplateEditor from "./pages/TemplateEditor";
import Masters from "./pages/Masters";
import Learning from "./pages/Learning";
import SettingsPage from "./pages/Settings";
import Login from "./pages/Login";

function useHashRoute(): string[] {
  const parse = () => (window.location.hash.replace(/^#\/?/, "") || "home").split("/");
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const h = () => setRoute(parse());
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);
  return route;
}

const NAV = [
  { key: "home", label: "ホーム", icon: "⌂" },
  { key: "documents", label: "注文書", icon: "▤" },
  { key: "templates", label: "テンプレート・ROI", icon: "⬚" },
  { key: "masters", label: "マスタ", icon: "☰" },
  { key: "learning", label: "学習", icon: "↻" },
  { key: "settings", label: "設定", icon: "⚙" },
];

export default function App() {
  const [info, setInfo] = useState<Info | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploadTick, setUploadTick] = useState(0);
  const route = useHashRoute();
  const toast = useToast();

  const loadInfo = useCallback(() => api.get<Info>("/api/info").then(setInfo).catch(() => setInfo(null)), []);
  useEffect(() => {
    loadInfo();
    const h = () => loadInfo();
    window.addEventListener("eagle:unauthorized", h);
    return () => window.removeEventListener("eagle:unauthorized", h);
  }, [loadInfo]);

  const upload = useCallback(async (files: File[]) => {
    if (!files.length) return;
    try {
      const res = await uploadFiles(files);
      const ok = res.results.filter((r) => !r.error);
      const ng = res.results.filter((r) => r.error);
      if (ok.length) toast(`${ok.length}件を取り込みました。順に読み取ります`, "ok");
      ng.forEach((r) => toast(r.error!, "error"));
      setUploadTick((t) => t + 1);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }, [toast]);

  if (!info) return <div className="boot">起動中…</div>;
  if (info.auth_required && !info.user) return <Login info={info} onLogin={loadInfo} />;

  const canEdit = info.user?.role !== "viewer";
  const onDragOver = (e: DragEvent) => {
    if (!canEdit || !e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    setDragging(true);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (canEdit) upload(Array.from(e.dataTransfer.files));
  };

  const [page, arg] = route;
  let body;
  if (page === "documents" && arg) body = <DocumentDetail id={Number(arg)} info={info} />;
  else if (page === "documents") body = <Documents info={info} tick={uploadTick} />;
  else if (page === "templates" && arg) body = <TemplateEditor id={arg === "new" ? null : Number(arg)} />;
  else if (page === "templates") body = <Templates />;
  else if (page === "masters") body = <Masters info={info} />;
  else if (page === "learning") body = <Learning />;
  else if (page === "settings") body = <SettingsPage info={info} tab={arg} />;
  else body = <Home info={info} onUpload={upload} tick={uploadTick} />;

  return (
    <div className="shell" onDragOver={onDragOver} onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)} onDrop={onDrop}>
      <aside className="side">
        <div className="brand">
          <img src="/eagle.png" alt="" />
          <div>
            <div className="brand-name">E.A.G.L.E.</div>
            <div className="brand-sub">{info.mode === "server" ? "Web版" : "アプリ版"} {info.version}</div>
          </div>
        </div>
        <nav>
          {NAV.map((n) => (
            <a key={n.key} href={`#/${n.key}`} className={page === n.key || (n.key === "home" && !NAV.some((x) => x.key === page)) ? "active" : ""}>
              <span className="nav-icon">{n.icon}</span>
              {n.label}
            </a>
          ))}
        </nav>
        <div className="side-foot">
          {info.user && info.auth_required && (
            <>
              <div className="who">{info.user.display_name}<small>{info.user.role}</small></div>
              <button className="btn small ghost" onClick={() => api.post("/api/auth/logout").then(loadInfo)}>ログアウト</button>
            </>
          )}
          <div className="local-note">読み取りはローカルで行い、データを外部へ送信しません</div>
        </div>
      </aside>
      <main className="main">{body}</main>
      {dragging && (
        <div className="drop-overlay">
          <div>ここにドロップして取り込み<small>PDF・画像・メール(.eml)・Excel など</small></div>
        </div>
      )}
    </div>
  );
}
