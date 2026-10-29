"""VLM 推論クライアント。

対応する推論先:
  - llama_cpp : 同梱の llama.cpp (llama-server)。OpenAI 互換 API をローカルで起動して使う
  - openai    : OpenAI 互換の推論サーバー(LAN 内の vLLM / LM Studio / 別の PC の llama-server 等)
  - ollama    : Ollama の /api/chat

どの推論先でも「JSON だけを返させる」「空返しは温度を上げて再試行する」を共通で行う。
(温度0のまま再送すると決定的に同じ空が返るため、再試行時だけ 0 → 0.3 → 0.6 と上げる)
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from typing import Any, Callable

import httpx

LOG = logging.getLogger("eagle.vlm")


class VlmError(RuntimeError):
    pass


class VlmUnavailable(VlmError):
    """推論先に接続できない(未起動・タイムアウト・モデル未導入)。"""


def image_to_b64(img, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def parse_json_loose(text: str) -> Any:
    """モデル出力から JSON を取り出す。コードフェンス・前後の説明文・末尾カンマを許容する。"""
    if not text:
        return {}
    t = _FENCE_RE.sub("", text.strip()).strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
    if start < 0:
        return {}
    end = max(t.rfind("}"), t.rfind("]"))
    if end <= start:
        return {}
    chunk = t[start:end + 1]
    for candidate in (chunk, re.sub(r",\s*([}\]])", r"\1", chunk)):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return {}


def _is_local_url(url: str) -> bool:
    host = (httpx.URL(url).host or "").lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local") or host.endswith(".lan"):
        return True
    if re.fullmatch(r"10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+", host):
        return True
    return "." not in host  # LAN 内のホスト名(ドット無し)


class VlmClient:
    def __init__(self, vlm_settings: dict, llama_manager=None):
        self.cfg = vlm_settings
        self.backend = str(vlm_settings.get("backend") or "llama_cpp")
        self.llama = llama_manager
        self.json_mode = str(vlm_settings.get("json_mode") or "schema")
        self.retry_empty = int(vlm_settings.get("retry_empty", 2))
        self.max_tokens = int(vlm_settings.get("max_tokens", 4096))
        self.timeout = float(vlm_settings.get("timeout_sec", 900))
        self.calls = 0

    # ── 接続先 ────────────────────────────────────────────────────────
    def _endpoint(self) -> tuple[str, str, dict]:
        """(種類, URL, ヘッダ) を返す。llama_cpp は必要なら llama-server を起動する。"""
        if self.backend == "llama_cpp":
            if self.llama is None:
                raise VlmUnavailable("llama.cpp マネージャが初期化されていません")
            port = self.llama.ensure_ready()
            return "openai", f"http://127.0.0.1:{port}/v1/chat/completions", {}
        if self.backend == "ollama":
            o = self.cfg.get("ollama", {})
            base = str(o.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
            return "ollama", base + "/api/chat", {}
        o = self.cfg.get("openai", {})
        base = str(o.get("base_url") or "").rstrip("/")
        if not base:
            raise VlmUnavailable("OpenAI 互換サーバーの URL が未設定です")
        headers = {"Authorization": f"Bearer {o['api_key']}"} if o.get("api_key") else {}
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        if not _is_local_url(url):
            LOG.warning("VLM の接続先が LAN・ローカル以外の可能性があります: %s", url)
        return "openai", url, headers

    def model_name(self) -> str:
        if self.backend == "llama_cpp":
            return (self.llama.current_model() if self.llama else "") or "llama.cpp"
        if self.backend == "ollama":
            return str(self.cfg.get("ollama", {}).get("model") or "")
        return str(self.cfg.get("openai", {}).get("model") or "")

    # ── ペイロード ───────────────────────────────────────────────────
    def _response_format(self, schema: dict | None) -> dict | None:
        if self.json_mode == "none":
            return None
        if self.json_mode == "schema" and schema:
            return {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}}
        return {"type": "json_object"}

    def _payload_openai(self, system: str, user: str, images: list[str], temp: float, schema) -> dict:
        content: list[dict] = [{"type": "text", "text": user}]
        for b in images:
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + b}})
        payload: dict = {
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "stream": False,
            "temperature": temp,
            "max_tokens": self.max_tokens,
        }
        model = self.cfg.get("openai", {}).get("model") if self.backend == "openai" else None
        if model:
            payload["model"] = model
        rf = self._response_format(schema)
        if rf:
            payload["response_format"] = rf
        if self.backend == "llama_cpp":
            # Qwen3 系の思考モードを確実に切る(Instruct では既定で無効だが念のため)
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    def _payload_ollama(self, system: str, user: str, images: list[str], temp: float, schema) -> dict:
        o = self.cfg.get("ollama", {})
        keep = str(o.get("keep_alive") or "30m")
        fmt: Any = "json"
        if self.json_mode == "schema" and schema:
            fmt = schema
        elif self.json_mode == "none":
            fmt = None
        payload = {
            "model": o.get("model") or "qwen3-vl:8b",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user, **({"images": images} if images else {})},
            ],
            "stream": False,
            # 思考モードは大画像で数千トークン生成して空返しの原因になるため無効化する
            "think": False,
            "keep_alive": int(keep) if re.fullmatch(r"-?\d+", keep) else keep,
            "options": {"temperature": temp, "num_ctx": int(self.cfg.get("llama", {}).get("ctx", 16384))},
        }
        if fmt is not None:
            payload["format"] = fmt
        return payload

    @staticmethod
    def _parse(kind: str, body: dict) -> tuple[str, str]:
        if kind == "openai":
            ch = (body.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            raw = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "").strip()
            return raw, str(ch.get("finish_reason") or "")
        msg = body.get("message") or {}
        # think:false でも答えが thinking 側に入る版があるため、content が空なら thinking を読む
        raw = (msg.get("content") or "").strip() or (msg.get("thinking") or "").strip()
        return raw, str(body.get("done_reason") or "")

    # ── 呼び出し ────────────────────────────────────────────────────
    def chat_json(self, system: str, user: str, images: list[str] | None = None, schema: dict | None = None,
                  want: Callable[[dict], bool] | None = None) -> dict:
        """1回の推論で JSON(dict) を得る。want(d) が False(空返し)なら温度を上げて再試行する。"""
        images = images or []
        kind, url, headers = self._endpoint()
        last: dict = {}
        for attempt in range(self.retry_empty + 1):
            temp = 0.0 if attempt == 0 else min(0.6, 0.3 * attempt)
            build = self._payload_openai if kind == "openai" else self._payload_ollama
            payload = build(system, user, images, temp, schema)
            body = self._post(url, payload, headers)
            raw, finish = self._parse(kind, body)
            data = parse_json_loose(raw)
            if not raw:
                LOG.warning("VLM が本文を返しませんでした(finish_reason=%s)", finish)
            if finish == "length":
                LOG.warning("VLM の出力が上限で打ち切られました。max_tokens を増やしてください")
            last = data if isinstance(data, dict) else {"items": data} if isinstance(data, list) else {}
            if want is None or want(last):
                return last
            if attempt < self.retry_empty:
                LOG.info("VLM 空返し → 再試行 %d/%d", attempt + 1, self.retry_empty)
        return last

    def _post(self, url: str, payload: dict, headers: dict) -> dict:
        self.calls += 1
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=10.0)) as c:
                    r = c.post(url, json=payload, headers=headers)
                if r.status_code == 503 and attempt < 2:   # llama-server のモデル読込中
                    time.sleep(3 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise VlmError(f"VLM がエラーを返しました HTTP {r.status_code}: {r.text[:300]}")
                return r.json()
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt < 2:
                    LOG.warning("VLM に接続できません。再試行します(%d/3): %s", attempt + 1, exc)
                    time.sleep(3 * (attempt + 1))
        raise VlmUnavailable(f"VLM に接続できません: {last_exc}")

    def ping(self) -> dict:
        """接続テスト(テキストのみの短い推論)。"""
        t0 = time.time()
        res = self.chat_json("指定JSONのみ出力します。", '次のJSONをそのまま返してください: {"ok": true}',
                             schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})
        return {"ok": bool(res.get("ok")), "sec": round(time.time() - t0, 2), "model": self.model_name()}
