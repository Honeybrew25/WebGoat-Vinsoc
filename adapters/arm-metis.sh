#!/usr/bin/env bash
# ============================================================================
#  adapters/arm-metis.sh — chạy Arm Metis một lần
# ============================================================================
#  Hợp đồng adapter: xem đầu file adapters/datadog-saist.sh
#  ĐẦU RA BẮT BUỘC: $RUN_DIR/raw_output.sarif
# ============================================================================
set -euo pipefail

METIS_DIR="$ROOT_DIR/tools/arm-metis"

# venv của uv đặt binary ở Scripts/ (Windows) hoặc bin/ (Linux/macOS). Dò cả hai
# để adapter không khoá cứng vào một hệ điều hành.
if [[ -x "$METIS_DIR/.venv/Scripts/metis.exe" ]]; then
  METIS_BIN="$METIS_DIR/.venv/Scripts/metis.exe"
elif [[ -x "$METIS_DIR/.venv/bin/metis" ]]; then
  METIS_BIN="$METIS_DIR/.venv/bin/metis"
else
  echo "[x] Chưa thấy binary metis trong $METIS_DIR/.venv — chạy: bash scripts/stage4_setup_tools.sh metis" >&2
  exit 1
fi

# --- Vì sao provider là "vllm" chứ không phải "openai" ------------------------
# Proxy LiteLLM của ta đo token ở endpoint OpenAI-compatible
# (/v1/chat/completions) — đó là nơi callback token_logger chạy. Metis có nhiều
# provider nói giao thức này; ta chọn "vllm" vì CONFIG_SPEC của nó BẮT BUỘC khai
# base_url (required_keys), trong khi provider "openai" mặc định trỏ thẳng
# api.openai.com. Nếu lỡ tay thiếu base_url với provider "openai", Metis sẽ gọi
# thẳng OpenAI, VÒNG QUA proxy — call vẫn chạy ngon, chỉ là ta mất sạch số liệu
# token mà không có dấu hiệu gì báo lỗi.
# Chính tài liệu Metis cũng khuyến nghị "front your deployment with a LiteLLM
# proxy so Metis only ever speaks OpenAI-compatible JSON over a single /v1".
# => Model thật VẪN là gemini-3.1-flash-lite (proxy định tuyến); chỉ khác nhãn provider.
#
# --- Vì sao KHÔNG bật index/embeddings ---------------------------------------
# Index (RAG) của Metis tắt mặc định và cần embedding provider riêng. Bật lên sẽ
# kéo thêm MỘT model khác (embedding) vào phép đo -> phá vỡ "cùng một model".
# Giữ tắt và ghi rõ đây là bất đối xứng đã biết (xem docs/stage4-*.md).

# --- Knob Stage 8 (tùy chọn; rỗng = giữ nguyên baseline Stage 4) --------------
METIS_ENGINE_BLOCK=""
if [[ -n "${METIS_MAX_WORKERS:-}" || -n "${METIS_REVIEW_INCLUDE:-}" ]]; then
  METIS_ENGINE_BLOCK="metis_engine:"
  if [[ -n "${METIS_MAX_WORKERS:-}" ]]; then
    METIS_ENGINE_BLOCK+=$'\n  max_workers: '"$METIS_MAX_WORKERS"
  fi
  if [[ -n "${METIS_REVIEW_INCLUDE:-}" ]]; then
    METIS_ENGINE_BLOCK+=$'\n  review_code_include_paths:\n    - '"\"$METIS_REVIEW_INCLUDE\""
  fi
  if [[ -n "${METIS_REVIEW_EXCLUDES:-}" ]]; then
    METIS_ENGINE_BLOCK+=$'\n  review_code_exclude_paths:'
    while IFS= read -r pattern; do
      if [[ -n "$pattern" ]]; then
        METIS_ENGINE_BLOCK+=$'\n    - '"\"$pattern\""
      fi
    done <<<"$METIS_REVIEW_EXCLUDES"
  fi
fi

# Metis đọc metis.yaml từ THƯ MỤC LÀM VIỆC -> sinh file ngay trong RUN_DIR.
cat > "$RUN_DIR/metis.yaml" <<EOF
$METIS_ENGINE_BLOCK

llm_provider:
  name: "vllm"                      # provider OpenAI-compatible
  base_url: "${PROXY_BASE_URL}/v1"  # trỏ về LiteLLM proxy
  model: "${MODEL_ALIAS}"           # alias -> quy chiếu token về tool này
  api_key_env: "METIS_PROXY_KEY"
  # LƯU Ý: provider vllm chỉ nhận "default_headers" (xem CONFIG_SPEC.copy_keys
  # trong src/metis/providers/vllm.py). Key "additional_headers" CHỈ tồn tại ở
  # provider "gemini" — đặt nhầm tên thì header bị bỏ im lặng, không báo lỗi,
  # và mọi call sẽ về tool "unknown" trong calls.jsonl.
  default_headers:
    ${TOOL_HEADER}: "arm-metis"     # quy chiếu tool cách 1 (header)

query:
  model: "${MODEL_ALIAS}"
  temperature: 0.0                  # khớp config/benchmark.yaml
  max_tokens: 8192
EOF

export METIS_PROXY_KEY="$PROXY_KEY"
# Một số build đọc key qua biến chuẩn của provider OpenAI-compatible.
# LƯU Ý: đây là key của PROXY (LITELLM_MASTER_KEY), KHÔNG phải key nhà cung cấp.
# Tên biến mang chữ "OPENAI" chỉ vì Metis nói giao thức OpenAI — không liên quan
# gì tới việc model thật là Gemini hay OpenAI. Đừng đổi thành GEMINI_API_KEY.
export VLLM_API_KEY="$PROXY_KEY"
export OPENAI_API_KEY="$PROXY_KEY"

# Chạy từ RUN_DIR, nhưng vẫn trỏ --config TƯỜNG MINH: dựa vào thư mục làm việc là
# ngầm định, nếu Metis đổi thứ tự tìm config thì ta lặng lẽ chạy sai cấu hình.
#   --non-interactive --command "review_code" : quét toàn bộ codebase, không hỏi
#   --triage : bật pha phân loại + xuất SARIF có annotate
cd "$RUN_DIR"
exec "$METIS_BIN" \
  --config "$RUN_DIR/metis.yaml" \
  --non-interactive \
  --triage \
  --codebase-path "$TARGET_DIR" \
  --command "review_code" \
  --output-file "$RUN_DIR/raw_output.sarif"
