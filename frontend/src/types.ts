export type Status = "queued" | "processing" | "extracted" | "needs_review" | "confirmed" | "skipped" | "failed";

export interface User {
  id: number;
  username: string;
  display_name: string;
  role: "admin" | "editor" | "viewer";
}

export interface Info {
  app: string;
  version: string;
  mode: "desktop" | "server";
  auth_required: boolean;
  user: User | null;
  vlm_backend: string;
  needs_setup: boolean;
}

export interface DocSummary {
  id: number;
  filename: string;
  status: Status;
  status_label: string;
  status_detail: string;
  order_number: string;
  order_date: string;
  buyer_company: string;
  ship_to_company: string;
  buyer_code: string;
  total: number | string;
  line_count: number;
  amount: number | null;
  template: string;
  template_id: number | null;
  warnings: number;
  source: string;
  created_at: string;
  processed_at: string | null;
  confirmed_at: string | null;
  synced_at: string | null;
  page_count: number;
}

export interface Line {
  id?: number;
  line_no: number;
  page: number;
  order_number: string;
  product_code: string;
  raw_code: string;
  product_name: string;
  quantity: number | null;
  unit: string;
  unit_price: number | null;
  amount: number | null;
  due_date: string;
  notes: string;
  master_matched?: boolean;
  match_method?: string;
  extra: Record<string, unknown>;
  extracted?: Record<string, unknown>;
}

export interface PageMeta {
  index: number;
  width: number;
  height: number;
  kind: "image" | "text";
  text_layer: boolean;
}

export interface MatchInfo {
  code: string;
  label: string;
  score: number;
  why: string;
  runner_up: string;
}

export interface RoiResult {
  roi_id: string;
  label: string;
  field: string;
  page: number;
  value: unknown;
  source: string;
  box: number[];
  note: string;
}

export interface DocDetail extends Omit<DocSummary, "warnings"> {
  header: Record<string, unknown>;
  extracted_header: Record<string, unknown>;
  warnings: string[];
  lines: Line[];
  doc_title: string;
  model_name: string;
  processing_sec: number;
  file_type: string;
  confirmed_by: string;
  pages: PageMeta[];
  content_bbox: Record<string, number[]>;
  buyer_match: MatchInfo | null;
  ship_to_match: MatchInfo | null;
  template_match: string;
  roi: RoiResult[];
  timings: Record<string, number>;
  vlm_calls: number;
  companies: { name: string; department?: string; contact?: string; address?: string; tel?: string; role: string }[];
  template_locked: boolean;
  job: { id: number; status: string; progress: number; message: string } | null;
}

export interface Roi {
  id: string;
  label: string;
  field: string;
  page: number;
  x: number;
  y: number;
  w: number;
  h: number;
  mode: "auto" | "text" | "vlm";
  merge: "override" | "fallback";
  instruction: string;
  regex: string;
  pad: number;
  enabled: boolean;
}

export interface Template {
  id: number;
  name: string;
  description: string;
  enabled: boolean;
  priority: number;
  match_keywords: string[];
  anchor: "page" | "content";
  reference: { content_bbox?: Record<string, number[]> };
  sample_document_id: number | null;
  rois: Roi[];
  prompt_hint: string;
  defaults: Record<string, string>;
  overrides: Record<string, string>;
  patterns: Record<string, string>;
  documents?: number;
}

export interface FieldInfo {
  key: string;
  label: string;
  kind: string;
  level: string;
  description: string;
  builtin: boolean;
}

export interface FieldsPayload {
  header: FieldInfo[];
  line: FieldInfo[];
  roi_blocks: { key: string; label: string }[];
  custom?: (FieldInfo & { id: number; enabled: boolean; sort_order: number })[];
}

export interface JobsStatus {
  running: { job_id: number; document_id: number; progress: number; message: string; elapsed: number } | null;
  queued: number;
  recent: { id: number; document_id: number; status: string; progress: number; message: string }[];
  alive: boolean;
}

export interface WatchStatus {
  running: boolean;
  folders: { path: string; recursive: boolean; polling: boolean; ok: boolean; error: string }[];
  pending: number;
  processed: number;
  errors: string[];
}

export type Settings = Record<string, any>;
