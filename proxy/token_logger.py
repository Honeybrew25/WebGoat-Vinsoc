"""
token_logger.py — custom callback cho LiteLLM proxy (Giai đoạn 3).

Nhiệm vụ: mỗi khi 1 tool gọi LLM qua proxy, ghi MỘT dòng JSONL gồm:
  - tool nào gọi (đọc từ header x-tool-name)
  - model, số token vào/ra (LẤY TỪ usage THẬT của API, không đoán)
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


# Tên model mà SAIST tự nhảy sang khi bị 429. Trong litellm_config.yaml alias này
# đã được trỏ về cùng model, nên nó vô hại — nhưng ta vẫn ĐÁNH DẤU từng call
# để báo cáo cuối nói được "có N call đi qua đường fallback", thay vì im lặng.
SAIST_FALLBACK_ALIAS = "openai/gpt-4.1-nano"


def _requested_alias(kwargs):
    """
    Lấy TÊN MODEL MÀ TOOL ĐÃ YÊU CẦU (alias), không phải model thật.

    Vì sao cần: khi callback chạy, LiteLLM đã định tuyến xong và `kwargs["model"]`
    là model THẬT ở phía sau ("gemini-31-flash-lite") — alias đã bị nuốt mất.
    Dùng nó để suy ra tool thì mọi call đều rơi về "unknown". Alias gốc còn nằm ở
    metadata.model_group và ở body request thô mà proxy nhận được.
    """
    lp = kwargs.get("litellm_params", {}) or {}
    candidates = [
        _dig(lp, "metadata", "model_group"),
        _dig(kwargs, "metadata", "model_group"),
        kwargs.get("model_group"),
        _dig(lp, "proxy_server_request", "body", "model"),
        _dig(kwargs, "proxy_server_request", "body", "model"),
    ]
    for c in candidates:
        if c:
            return str(c)
    return None


def _tool_from_model_alias(model_name):
    """
    Suy ra tool từ ALIAS model, dùng khi tool không gắn được header x-tool-name
    (ví dụ SAIST). Alias dạng "<model-id>-<tool-suffix>", vd:
        gemini-31-flash-lite-saist  -> "datadog-saist"
        gemini-31-flash-lite-metis  -> "arm-metis"
    Alias trung tính (không hậu tố) -> None để rơi về "unknown"/header.
    """
    if not model_name:
        return None
    # Fallback cứng của SAIST khi gặp 429 (xem litellm_config.yaml). Alias này đã
    # được trỏ về cùng model nên KHÔNG phá "cùng model", nhưng call vẫn là
    # của SAIST -> phải quy đúng tool, đừng để rơi về "unknown".
    if str(model_name) == SAIST_FALLBACK_ALIAS:
        return "datadog-saist"
    # Bảng ánh xạ hậu tố -> id tool trong config/benchmark.yaml
    suffix_map = {
        "saist": "datadog-saist",
        "metis": "arm-metis",
        "vulnhuntr": "vulnhuntr",
        # Giai đoạn 7 — KHÔNG phải tool SAST. Tách riêng để chi phí chấm điểm
        # không bị cộng nhầm vào chi phí của tool nào.
        "judge": "_judge",
    }
    for suffix, tool_id in suffix_map.items():
        if str(model_name).endswith("-" + suffix):
            return tool_id
    return None


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
            # model THẬT (sau định tuyến) vs ALIAS tool đã yêu cầu — hai thứ khác nhau.
            real_model = kwargs.get("model")
            alias = _requested_alias(kwargs)
            # Quy chiếu tool: ưu tiên header x-tool-name; nếu tool không gắn được
            # header thì suy từ ALIAS (xem litellm_config.yaml). Phải dùng alias,
            # không dùng real_model — mọi alias đều trỏ về cùng một model thật nên
            # real_model không phân biệt được tool nào.
            tool = _extract_tool_name(kwargs)
            if tool == "unknown":
                tool = _tool_from_model_alias(alias) or "unknown"
            row = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "tool": tool,
                "model": real_model,
                # Ghi cả alias để GĐ5 đối chiếu được với run_meta.json.
                "requested_model": alias,
                # True = call này đi qua đường fallback của SAIST. Model vẫn đúng
                # (alias trỏ về cùng model), nhưng phải đếm được để báo cáo.
                "via_fallback": alias == SAIST_FALLBACK_ALIAS,
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
