"""VLM(画像言語モデル)関連。"""
from __future__ import annotations

from ..config import boot
from .client import VlmClient, VlmError, VlmUnavailable, image_to_b64, parse_json_loose
from .llama_server import LlamaServerManager
from .setup import SetupRunner

_llama: LlamaServerManager | None = None
_setup = SetupRunner()


def llama_manager() -> LlamaServerManager:
    global _llama
    if _llama is None:
        b = boot()
        _llama = LlamaServerManager(b.bin_dir, b.models_dir, b.logs_dir)
    return _llama


def setup_runner() -> SetupRunner:
    return _setup


def make_client(vlm_settings: dict) -> VlmClient:
    mgr = llama_manager()
    mgr.configure(vlm_settings.get("llama", {}))
    return VlmClient(vlm_settings, mgr)


__all__ = ["VlmClient", "VlmError", "VlmUnavailable", "image_to_b64", "parse_json_loose", "llama_manager",
           "make_client", "setup_runner"]
