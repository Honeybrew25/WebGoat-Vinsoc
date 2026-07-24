#!/usr/bin/env bash
# ============================================================================
#  adapters/datadog-saist.sh — chạy SAIST (Datadog) một lần
# ============================================================================
#  HỢP ĐỒNG ADAPTER (orchestrator truyền vào qua biến môi trường):
#    TARGET_DIR      đường tuyệt đối tới mã nguồn cần quét (host)
#    RUN_DIR         thư mục ghi kết quả của lần chạy này (host)
#    MODEL_ALIAS     alias model trên proxy -> dùng để quy chiếu token về tool
#    PROXY_BASE_URL  vd http://127.0.0.1:4000
#    PROXY_KEY       LITELLM_MASTER_KEY
#  ĐẦU RA BẮT BUỘC: $RUN_DIR/raw_output.sarif  + exit code phản ánh thành/bại
# ============================================================================
set -euo pipefail

# --- Vì sao chạy trong Docker ------------------------------------------------
# SAIST viết bằng Go; host chưa cài Go. Image sast-bench/saist:pinned được
# stage4_setup_tools.sh build sẵn -> không cần toolchain trên máy.
#
# --- Vì sao base URL là host.docker.internal ---------------------------------
# Trong container, 127.0.0.1 là chính container đó, KHÔNG phải máy host.
# host.docker.internal mới trỏ về host - nơi LiteLLM proxy đang lắng nghe.
PROXY_HOST_FROM_CONTAINER="${PROXY_BASE_URL/127.0.0.1/host.docker.internal}"
PROXY_HOST_FROM_CONTAINER="${PROXY_HOST_FROM_CONTAINER/localhost/host.docker.internal}"

# MSYS_NO_PATHCONV=1: Git Bash trên Windows hay "dịch" /work thành C:\work,
# làm hỏng đường dẫn trong container. Tắt tính năng đó đi.
export MSYS_NO_PATHCONV=1

# Lưu ý các cờ:
#   --local-prompts : dùng bộ rule nhúng sẵn trong binary thay vì tải từ API
#                     Datadog. Cần cho tái lập (rule trên server có thể đổi
#                     theo thời gian) và cho chủ trương "không dùng internet".
#   KHÔNG dùng --skip-indexing : giữ cross-file context, đây là thế mạnh của
#                     SAIST, tắt đi là tự làm yếu tool -> so sánh không công bằng.
#   --detection-model / --validation-model : SAIST có 2 pha detect -> validate.
#                     Cả hai đều dùng CÙNG alias để mọi token đều quy về tool này.
exec docker run --rm \
  --add-host=host.docker.internal:host-gateway \
  -v "$TARGET_DIR":/work/target:ro \
  -v "$RUN_DIR":/out \
  -e OPENAI_API_KEY="$PROXY_KEY" \
  sast-bench/saist:pinned \
    --directory /work/target \
    --output /out/raw_output.sarif \
    --detection-model "$MODEL_ALIAS" \
    --validation-model "$MODEL_ALIAS" \
    --openai-base-url "$PROXY_HOST_FROM_CONTAINER" \
    --api-key "$PROXY_KEY" \
    --local-prompts
