"""
token_logger.py — custom callback cho LiteLLM proxy (Giai đoạn 3).

Nhiệm vụ: mỗi khi 1 tool gọi LLM qua proxy, ghi MỘT dòng JSONL gồm:
  - tool nào gọi (đọc từ header x-tool-name)
  - model, số token vào/ra (LẤY TỪ usage THẬT của Gemini, không đoán)
  - latency (thời gian call)
  - cost ước tính (LiteLLM tự tính theo bảng giá)

Vì sao cần: thay vì tin con số mỗi tool tự khai (mỗi ông một phách, có ông giấu),
ta đo KHÁCH QUAN ngay tại điểm mọi call bắt buộc đi qua.

File log: results/proxy_logs/calls.jsonl  (mỗi dòng độc lập -> jq xử lý ngon).
"""
import json
import os
import threading
from datetime import datetime, timezone

from litellm.integrations.custom_logger import CustomLogger

# --- Xác định đường ghi log ----------------------------------------------------
# Ưu tiên biến môi trường PROXY_CALL_LOG (dùng khi chạy trong Docker, đường dẫn
# container khác host). Nếu không có thì suy ra results/proxy_logs/calls.jsonl.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)
_LOG_PATH = os.environ.get(
    "PROXY_CALL_LOG",
    os.path.join(_ROOT, "results", "proxy_logs", "calls.jsonl"),
)
os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)

# Ghi file từ nhiều async worker -> cần khoá để dòng không chèn vào nhau.
_write_lock = threading.Lock()


def _dig(d, *keys, default=None):
    """Lấy d[k1][k2]... an toàn, trả default nếu thiếu bất kỳ tầng nào."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _extract_tool_name(kwargs) -> str:
    """
    Tìm header x-tool-name mà adapter gắn vào request để biết call của tool nào.
    LiteLLM để header ở vài chỗ khác nhau tuỳ phiên bản -> thử lần lượt.
    """
    lp = kwargs.get("litellm_params", {}) or {}
    candidates = [
        _dig(lp, "proxy_server_request", "headers"),
        _dig(lp, "metadata", "headers"),
        _dig(kwargs, "proxy_server_request", "headers"),
        _dig(kwargs, "metadata", "headers"),
    ]
    for headers in candidates:
        if isinstance(headers, dict):
            for hk, hv in headers.items():
                if hk.lower() == "x-tool-name" and hv:
                    return str(hv)
    # Fallback: có thể tool truyền qua metadata.tags hoặc user field.
    tags = _dig(lp, "metadata", "tags")
    if isinstance(tags, list) and tags:
        return str(tags[0])
    return "unknown"


def _extract_usage(response_obj):
    """Lấy prompt/completion/total tokens từ usage THẬT của response."""
    usage = getattr(response_obj, "usage", None)
    if usage is None and isinstance(response_obj, dict):
        usage = response_obj.get("usage")
    if usage is None:
        return 0, 0, 0
    get = (lambda k: getattr(usage, k, None)) if not isinstance(usage, dict) else usage.get
    p = get("prompt_tokens") or 0
    c = get("completion_tokens") or 0
    t = get("total_tokens") or (p + c)
    return int(p), int(c), int(t)


class TokenLogger(CustomLogger):
    """Ghi 1 dòng JSONL cho mỗi call LLM thành công."""

    def _record(self, kwargs, response_obj, start_time, end_time):
        try:
            p_tok, c_tok, t_tok = _extract_usage(response_obj)
            latency = None
            if start_time and end_time:
                latency = (end_time - start_time).total_seconds()
            row = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "tool": _extract_tool_name(kwargs),
                "model": kwargs.get("model"),
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "total_tokens": t_tok,
                "latency_s": latency,
                # LiteLLM tự tính cost theo bảng giá model (USD). Có thể None.
                "cost_usd": kwargs.get("response_cost"),
                "call_id": _dig(kwargs, "litellm_call_id"),
            }
            with _write_lock:
                with open(_LOG_PATH, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as e:  # logger không được làm sập proxy
            print(f"[token_logger] lỗi khi ghi: {e}")

    # LiteLLM gọi 1 trong 2 hàm dưới tuỳ call sync/async.
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._record(kwargs, response_obj, start_time, end_time)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._record(kwargs, response_obj, start_time, end_time)


# Instance mà litellm_config.yaml trỏ tới: "token_logger.proxy_logger_instance"
proxy_logger_instance = TokenLogger()
