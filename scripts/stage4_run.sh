#!/usr/bin/env bash
# ============================================================================
#  stage4_run.sh — Giai đoạn 4 (bước 2): chạy benchmark & đo
# ============================================================================
#  Orchestrator gọi từng adapter trong adapters/, mỗi tool N lần, và ghi lại
#  wall-clock + lát cắt log proxy tương ứng cho TỪNG lần chạy.
#
#  Dùng:
#    bash scripts/stage4_run.sh --dry-run          # chỉ preflight, không chạy tool
#    bash scripts/stage4_run.sh                    # chạy mọi tool enabled
#    bash scripts/stage4_run.sh --tool arm-metis   # chỉ 1 tool
#    bash scripts/stage4_run.sh --repeats 1        # ghi đè run.repeats
#    bash scripts/stage4_run.sh --run-index 2      # chỉ chạy đúng run 2
#    bash scripts/stage4_run.sh --output-root PATH # chỉ nhận results/optimization
#
#  Yêu cầu: đã chạy stage2 (target), stage3 (proxy đang bật), stage4_setup_tools.
# ============================================================================
source "$(dirname "${BASH_SOURCE[0]}")/lib_common.sh"

BASE_URL="$(yaml_get 'proxy.base_url')"
TOOL_HEADER="$(yaml_get 'proxy.tool_tag_header')"
CALL_LOG="$ROOT_DIR/$(yaml_get 'proxy.call_log')"
TARGET_DIR="$ROOT_DIR/$(yaml_get 'target.local_path')"
TARGET_SHA="$(yaml_get 'target.sha')"
REPEATS="$(yaml_get 'run.repeats')"
TIMEOUT_S="$(yaml_get 'run.timeout_s')"

DRY_RUN=0
ONLY_TOOL=""
RUN_INDEX=""
OUTPUT_ROOT="$ROOT_DIR/results/findings"
OUTPUT_ROOT_EXPLICIT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --tool)    ONLY_TOOL="$2"; shift 2 ;;
    --repeats) REPEATS="$2"; shift 2 ;;
    --run-index) RUN_INDEX="$2"; shift 2 ;;
    --output-root)
      OUTPUT_ROOT="$2"
      OUTPUT_ROOT_EXPLICIT=1
      shift 2
      ;;
    *) c_err "Tham số lạ: $1"; exit 1 ;;
  esac
done

if [[ -n "$RUN_INDEX" && ! "$RUN_INDEX" =~ ^[1-9][0-9]*$ ]]; then
  c_err "--run-index phải là số nguyên dương."
  exit 1
fi

# Stage 8 được phép tái sử dụng runner nhưng tuyệt đối không được trỏ nhầm vào
# baseline. Chỉ chấp nhận output tùy chọn dưới results/optimization/.
if [[ $OUTPUT_ROOT_EXPLICIT -eq 1 ]]; then
  OUTPUT_ROOT="$(realpath -m "$OUTPUT_ROOT")"
  case "$OUTPUT_ROOT/" in
    "$ROOT_DIR/results/optimization/"*) ;;
    *)
      c_err "--output-root phải nằm dưới $ROOT_DIR/results/optimization"
      exit 1
      ;;
  esac
fi
export BENCH_RESULTS_ROOT="$OUTPUT_ROOT"

# .env chứa LITELLM_MASTER_KEY mà adapter cần để gọi proxy.
ENV_FILE="$ROOT_DIR/proxy/.env"
[[ -f "$ENV_FILE" ]] || { c_err "Chưa có proxy/.env — xem docs/stage3-llm-proxy.md"; exit 1; }
set -a; source "$ENV_FILE"; set +a
[[ -n "${LITELLM_MASTER_KEY:-}" ]] || { c_err "LITELLM_MASTER_KEY trống."; exit 1; }
if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  c_err "GEMINI_API_KEY trong proxy/.env đang trống."
  c_err "Benchmark dùng gemini-3.1-flash-lite — lấy key ở https://aistudio.google.com/apikey"
  exit 1
fi

# ============================================================================
#  PREFLIGHT — thà dừng ở đây còn hơn chạy 40 phút rồi phát hiện sai cấu hình
# ============================================================================
preflight() {
  local ok=0

  # 1) Target đúng SHA đã pin. Quét nhầm phiên bản code -> mọi so sánh vô nghĩa.
  if [[ ! -d "$TARGET_DIR/.git" ]]; then
    c_err "Chưa có target tại $TARGET_DIR — chạy: bash scripts/stage2_setup_target.sh"
    ok=1
  else
    local cur
    cur="$(git -C "$TARGET_DIR" rev-parse HEAD)"
    if [[ "$cur" != "$TARGET_SHA" ]]; then
      c_err "Target đang ở $cur, KHÁC SHA đã pin $TARGET_SHA."
      ok=1
    else
      c_ok "Target đúng SHA đã pin (${TARGET_SHA:0:12})."
    fi
  fi

  # 2) Proxy sống. Không có proxy thì không đo được token -> chạy cũng vô ích.
  if curl -s -o /dev/null --max-time 5 "$BASE_URL/health/liveliness"; then
    c_ok "Proxy sống tại $BASE_URL"
  else
    c_err "Proxy không phản hồi — chạy: bash scripts/stage3_start_proxy.sh"
    ok=1
  fi

  # 3) MỌI alias trong benchmark.yaml phải tồn tại trên proxy.
  #    Alias thiếu -> tool gọi vào sẽ lỗi 400 giữa chừng, hoặc tệ hơn là token
  #    rơi về "unknown" và không quy chiếu được về tool nào.
  local models_json
  models_json="$(curl -s --max-time 5 -H "Authorization: Bearer $LITELLM_MASTER_KEY" "$BASE_URL/v1/models" || true)"
  local alias
  for alias in $(bench_cfg aliases); do
    if grep -q "\"$alias\"" <<<"$models_json"; then
      c_ok "Alias có trên proxy: $alias"
    else
      c_err "Alias '$alias' KHÔNG có trên proxy — thêm vào proxy/litellm_config.yaml rồi recreate container."
      ok=1
    fi
  done

  # 4) Mỗi tool enabled phải có adapter + đã cài xong.
  local tool
  for tool in $(tools_to_run); do
    [[ -f "$ROOT_DIR/adapters/$tool.sh" ]] \
      && c_ok "Có adapter: adapters/$tool.sh" \
      || { c_err "Thiếu adapters/$tool.sh"; ok=1; }

    case "$tool" in
      datadog-saist)
        docker image inspect sast-bench/saist:pinned >/dev/null 2>&1 \
          && c_ok "SAIST image đã build." \
          || { c_err "Chưa có image sast-bench/saist:pinned — chạy: bash scripts/stage4_setup_tools.sh saist"; ok=1; }
        ;;
      arm-metis)
        { [[ -x "$ROOT_DIR/tools/arm-metis/.venv/Scripts/metis.exe" ]] \
          || [[ -x "$ROOT_DIR/tools/arm-metis/.venv/bin/metis" ]]; } \
          && c_ok "Metis venv sẵn sàng." \
          || { c_err "Chưa cài Metis — chạy: bash scripts/stage4_setup_tools.sh metis"; ok=1; }
        ;;
    esac
  done

  return $ok
}

tools_to_run() {
  if [[ -n "$ONLY_TOOL" ]]; then echo "$ONLY_TOOL"; else bench_cfg tools-enabled; fi
}

# ============================================================================
#  CHẠY MỘT LẦN
# ============================================================================
#  Mấu chốt đo lường: ghi lại SỐ DÒNG của calls.jsonl ngay trước và ngay sau khi
#  chạy. Nhờ đó Giai đoạn 5 cắt được CHÍNH XÁC những call thuộc lần chạy này,
#  thay vì đoán mò theo mốc thời gian (dễ sai khi chạy nối tiếp nhau).
run_once() {
  local tool="$1" idx="$2" phase="$3"
  local run_dir="$OUTPUT_ROOT/$tool/run-$(printf '%02d' "$idx")-$phase"
  rm -rf "$run_dir"; mkdir -p "$run_dir"

  local alias log_before log_after start end status
  alias="$(bench_cfg tool "$tool" model_alias)"
  log_before=0; [[ -f "$CALL_LOG" ]] && log_before=$(wc -l < "$CALL_LOG" | tr -d ' ')

  c_info "[$tool] lần $idx ($phase) — alias=$alias"
  start=$(date +%s)
  set +e
  (
    export ROOT_DIR TARGET_DIR TOOL_HEADER
    export RUN_DIR="$run_dir"
    export MODEL_ALIAS="$alias"
    export PROXY_BASE_URL="$BASE_URL"
    export PROXY_KEY="$LITELLM_MASTER_KEY"
    # timeout: tool treo (ngồi đợi input, kẹt retry vô hạn) sẽ bị giết thay vì
    # làm đứng cả mẻ chạy. --kill-after: nếu SIGTERM không ăn thì SIGKILL.
    # Lưu ý với adapter chạy Docker: giết tiến trình `docker run` không đảm bảo
    # container chết theo, nên có bước dọn container mồ côi ở dưới.
    timeout --kill-after=30s "$TIMEOUT_S" bash "$ROOT_DIR/adapters/$tool.sh"
  ) >"$run_dir/stdout.log" 2>"$run_dir/stderr.log"
  status=$?
  set -e
  end=$(date +%s)

  # 124 = timeout gửi SIGTERM; 137 = bị SIGKILL sau --kill-after.
  local timed_out=false
  if [[ $status -eq 124 || $status -eq 137 ]]; then
    timed_out=true
    c_err "[$tool] lần $idx TREO quá ${TIMEOUT_S}s — đã giết."
    # Container mồ côi vẫn giữ cổng/CPU và làm lần chạy sau đo sai.
    local orphans
    orphans="$(docker ps -q --filter ancestor=sast-bench/saist:pinned 2>/dev/null || true)"
    if [[ -n "$orphans" ]]; then
      c_warn "Dọn container SAIST mồ côi..."
      docker kill $orphans >/dev/null 2>&1 || true
    fi
    # Metis chạy trên host, KHÔNG chết theo tiến trình cha. Đã xảy ra thật: giết
    # orchestrator xong mà metis.exe + python con vẫn chạy, khoá luôn thư mục
    # results/ nên không dọn được. Phải giết cả cây tiến trình.
    if command -v taskkill >/dev/null 2>&1; then
      taskkill //F //T //IM metis.exe >/dev/null 2>&1 || true
    else
      pkill -f "arm-metis/.venv" >/dev/null 2>&1 || true
    fi
  fi

  log_after=0; [[ -f "$CALL_LOG" ]] && log_after=$(wc -l < "$CALL_LOG" | tr -d ' ')

  # Đếm dấu hiệu rate limit trong stderr của tool. Một lần chạy dính 429 vẫn có
  # thể exit=0 và sinh SARIF hợp lệ, nhưng nội dung rỗng -> phải bắt riêng.
  local rate_limited=0 fallbacks=0
  if [[ -f "$run_dir/stderr.log" ]]; then
    rate_limited=$(grep -ciE "429|rate.?limit|RESOURCE_EXHAUSTED" "$run_dir/stderr.log" || true)
    fallbacks=$(grep -ci "switching to fallback" "$run_dir/stderr.log" || true)
  fi

  # Manifest: mọi thứ Giai đoạn 5 cần để quy chiếu, nằm cạnh chính kết quả.
  cat > "$run_dir/run_meta.json" <<EOF
{
  "tool": "$tool",
  "run_index": $idx,
  "phase": "$phase",
  "model_alias": "$alias",
  "target_sha": "$TARGET_SHA",
  "wall_clock_s": $((end - start)),
  "exit_code": $status,
  "timed_out": $timed_out,
  "timeout_s": $TIMEOUT_S,
  "rate_limit_hits": $rate_limited,
  "model_fallbacks": $fallbacks,
  "valid": $( [[ $status -eq 0 && $rate_limited -eq 0 && -s "$run_dir/raw_output.sarif" ]] && echo true || echo false ),
  "call_log_line_from": $((log_before + 1)),
  "call_log_line_to": $log_after,
  "llm_calls": $((log_after - log_before)),
  "started_at": "$(date -u -d "@$start" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)",
  "output_sarif": "raw_output.sarif"
}
EOF

  if [[ $status -eq 0 && -s "$run_dir/raw_output.sarif" && $rate_limited -eq 0 ]]; then
    c_ok "[$tool] lần $idx xong: $((end - start))s, $((log_after - log_before)) call LLM."
    # Cảnh báo sớm: 0 call LLM nghĩa là tool đã dùng cache, KHÔNG thật sự quét lại.
    # Lần chạy như vậy không đo được gì về chi phí -> phải biết ngay, đừng để tới
    # lúc đọc báo cáo mới phát hiện cột token toàn 0.
    if [[ $((log_after - log_before)) -eq 0 ]]; then
      c_warn "[$tool] lần $idx KHÔNG gọi LLM lần nào — nhiều khả năng tool đã cache kết quả."
    fi
  elif [[ $rate_limited -gt 0 ]]; then
    # ĐÃ TỪNG XẢY RA THẬT: tool trả exit=0 với SARIF hợp lệ nhưng chỉ 1 call LLM
    # và 0 finding, vì bị 429 chặn gần hết. "exit 0 + SARIF khác rỗng" là tiêu chí
    # QUÁ LỎNG — nó nhận nhầm một lần chạy rỗng là thành công.
    c_err "[$tool] lần $idx KHÔNG HỢP LỆ: dính rate limit ($rate_limited dấu hiệu trong stderr)."
    c_err "        Dữ liệu lần này KHÔNG dùng được. Kiểm tra hạn mức API trước khi chạy lại."
  else
    # KHÔNG exit: một tool hỏng không được làm chết cả mẻ chạy. Ghi nhận rồi đi tiếp;
    # Giai đoạn 5 sẽ thấy exit_code != 0 và loại lần chạy này khỏi thống kê.
    c_warn "[$tool] lần $idx THẤT BẠI (exit=$status). Xem $run_dir/stderr.log"
  fi
}

# ============================================================================
main() {
  c_info "Preflight..."
  if ! preflight; then
    c_err "Preflight thất bại — sửa các mục [x] ở trên rồi chạy lại."
    exit 1
  fi
  echo

  if [[ $DRY_RUN -eq 1 ]]; then
    local run_desc="$REPEATS lần"
    [[ -n "$RUN_INDEX" ]] && run_desc="run $RUN_INDEX"
    c_ok "--dry-run: preflight sạch. Output: $OUTPUT_ROOT"
    c_info "Sẽ chạy các tool sau, $run_desc:"
    for t in $(tools_to_run); do echo "    - $t (alias: $(bench_cfg tool "$t" model_alias))"; done
    exit 0
  fi

  for tool in $(tools_to_run); do
    echo
    c_info "===== $tool ====="
    local run_indices
    run_indices="$(if [[ -n "$RUN_INDEX" ]]; then echo "$RUN_INDEX"; else seq 1 "$REPEATS"; fi)"
    for i in $run_indices; do
      # Lần 1 = cold (cache tool trống), các lần sau = warm.
      # Tách ra vì lần cold gánh chi phí index/embed, trộn chung sẽ thổi phồng
      # thời gian trung bình và làm tool có index trông chậm hơn thực tế.
      local phase="warm"; [[ $i -eq 1 ]] && phase="cold"
      run_once "$tool" "$i" "$phase"
    done
  done

  echo
  c_ok "Chạy xong. Kết quả thô: $OUTPUT_ROOT/<tool>/run-NN-<phase>/"
  c_info "Tiếp theo (Giai đoạn 5): chuẩn hoá SARIF/JSON về JSONL chung + gộp token."
}

main
