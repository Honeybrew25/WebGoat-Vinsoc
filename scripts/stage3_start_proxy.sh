#!/usr/bin/env bash
# ============================================================================
#  stage3_start_proxy.sh — Giai đoạn 3: bật LiteLLM proxy đo token/time
# ============================================================================
#  Mặc định chạy bằng DOCKER (sạch, tái lập, tránh biên dịch Rust trên Windows).
#
#  Dùng:
#    bash scripts/stage3_start_proxy.sh              # bật proxy (docker compose up)
#    bash scripts/stage3_start_proxy.sh --down       # tắt proxy
#    bash scripts/stage3_start_proxy.sh --logs       # xem log container
#    bash scripts/stage3_start_proxy.sh --smoke-test # gửi 1 call thử, kiểm tra log token
#
#  Yêu cầu: đã tạo proxy/.env (copy .env.example) và điền GEMINI_API_KEY;
#           Docker Desktop đang chạy.
# ============================================================================
source "$(dirname "${BASH_SOURCE[0]}")/lib_common.sh"

PROXY_DIR="$ROOT_DIR/proxy"
ENV_FILE="$PROXY_DIR/.env"
BASE_URL="$(yaml_get 'proxy.base_url')"
TOOL_HEADER="$(yaml_get 'proxy.tool_tag_header')"
LOG_FILE="$ROOT_DIR/$(yaml_get 'proxy.call_log')"
# Model đọc từ single source of truth, KHÔNG hardcode ở đây.
MODEL_ID="$(yaml_get 'model.id')"

MODE="up"
for arg in "$@"; do
  case "$arg" in
    --down)       MODE="down" ;;
    --logs)       MODE="logs" ;;
    --smoke-test) MODE="smoke" ;;
    *) c_warn "Bỏ qua tham số lạ: $arg" ;;
  esac
done

# docker compose (v2) hay docker-compose (v1)?
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  c_err "Không thấy docker compose. Cài Docker Desktop và bật lên."
  exit 1
fi
COMPOSE_FILE="$PROXY_DIR/docker-compose.yml"

check_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    c_err "Chưa có proxy/.env. Chạy: cp proxy/.env.example proxy/.env rồi điền GEMINI_API_KEY."
    exit 1
  fi
  set -a; source "$ENV_FILE"; set +a
  if [[ -z "${GEMINI_API_KEY:-}" || "$GEMINI_API_KEY" == "your-gemini-api-key-here" ]]; then
    c_err "GEMINI_API_KEY trong proxy/.env chưa được điền."
    exit 1
  fi
  # Master key rỗng -> LiteLLM tra DB thay vì nhận Bearer -> "No connected db" (400).
  # Bắt ở đây để lỗi hiện ra đúng chỗ, thay vì hiện thành lỗi khó hiểu lúc gọi.
  if [[ -z "${LITELLM_MASTER_KEY:-}" ]]; then
    c_err "LITELLM_MASTER_KEY trong proxy/.env đang trống — proxy sẽ trả 'No connected db'."
    c_err "Đặt một giá trị bắt đầu bằng 'sk-', ví dụ: LITELLM_MASTER_KEY=sk-sast-bench-local"
    exit 1
  fi
}

case "$MODE" in
  down)
    c_info "Tắt proxy..."
    "${DC[@]}" -f "$COMPOSE_FILE" down
    c_ok "Đã tắt."
    ;;

  logs)
    "${DC[@]}" -f "$COMPOSE_FILE" logs -f --tail=100
    ;;

  up)
    check_env_file
    c_info "Kéo image & bật proxy (nền)..."
    "${DC[@]}" -f "$COMPOSE_FILE" up -d
    c_info "Chờ proxy sẵn sàng..."
    for i in $(seq 1 30); do
      if curl -s -o /dev/null "$BASE_URL/health/liveliness" 2>/dev/null \
         || curl -s -o /dev/null "$BASE_URL/" 2>/dev/null; then
        c_ok "Proxy sống tại $BASE_URL"
        break
      fi
      sleep 2
      [[ $i -eq 30 ]] && c_warn "Proxy chưa phản hồi sau 60s — xem: bash scripts/stage3_start_proxy.sh --logs"
    done
    echo
    c_info "Endpoint cho tool  : $BASE_URL/v1/chat/completions (OpenAI-compatible)"
    c_info "Model name         : $MODEL_ID"
    c_info "Header nhận diện tool: $TOOL_HEADER: <tên-tool>"
    c_info "Log token/latency  : $LOG_FILE"
    echo
    c_info "Kiểm tra nhanh: bash scripts/stage3_start_proxy.sh --smoke-test"
    ;;

  smoke)
    check_env_file
    c_info "Gửi 1 call thử tới $BASE_URL ..."
    BEFORE=0; [[ -f "$LOG_FILE" ]] && BEFORE=$(wc -l < "$LOG_FILE" | tr -d ' ')
    HTTP=$(curl -s -o /tmp/smoke_resp.json -w '%{http_code}' \
      "$BASE_URL/v1/chat/completions" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
      -H "$TOOL_HEADER: smoke-test" \
      -d "{\"model\":\"$MODEL_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word: OK\"}]}") || true
    if [[ "$HTTP" != "200" ]]; then
      c_err "Proxy trả HTTP $HTTP. Proxy đã 'up' chưa?"
      head -20 /tmp/smoke_resp.json 2>/dev/null
      exit 1
    fi
    c_ok "Proxy phản hồi 200. Nội dung trả:"
    head -c 300 /tmp/smoke_resp.json; echo
    sleep 1
    AFTER=0; [[ -f "$LOG_FILE" ]] && AFTER=$(wc -l < "$LOG_FILE" | tr -d ' ')
    if [[ "$AFTER" -gt "$BEFORE" ]]; then
      c_ok "✅ Logger đã ghi token. Dòng cuối trong calls.jsonl:"
      tail -1 "$LOG_FILE"
    else
      c_warn "Chưa thấy dòng log mới — kiểm tra callbacks trong litellm_config.yaml & mount /logs."
    fi
    ;;
esac
