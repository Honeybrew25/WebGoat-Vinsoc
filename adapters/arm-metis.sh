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

# --- Vì sao provider là "vllm" chứ không phải "gemini" ------------------------
# Metis có provider "gemini" nói giao thức Gemini gốc (generateContent). Proxy
# LiteLLM của ta đo token ở endpoint OpenAI-compatible (/v1/chat/completions) —
# đó là nơi callback token_logger chạy. Provider "vllm" của Metis chính là
# provider OpenAI-compatible: nó nói /v1 chuẩn OpenAI.
# Chính tài liệu Metis khuyến nghị "front your deployment with a LiteLLM proxy
# so Metis only ever speaks OpenAI-compatible JSON over a single /v1 endpoint".
# => Model VẪN là Gemini (proxy định tuyến); chỉ giao thức là OpenAI.
#
# --- Vì sao KHÔNG bật index/embeddings ---------------------------------------
# Index (RAG) của Metis tắt mặc định và cần embedding provider riêng. Bật lên sẽ
# kéo thêm MỘT model khác (embedding) vào phép đo -> phá vỡ "cùng một model".
# Giữ tắt và ghi rõ đây là bất đối xứng đã biết (xem docs/stage4-*.md).

# Metis đọc metis.yaml từ THƯ MỤC LÀM VIỆC -> sinh file ngay trong RUN_DIR.
cat > "$RUN_DIR/metis.yaml" <<EOF
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
