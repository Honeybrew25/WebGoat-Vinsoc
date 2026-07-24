#!/usr/bin/env bash
# ============================================================================
#  stage4_status.sh — xem mẻ chạy đang tới đâu
# ============================================================================
#  Đọc trạng thái từ CHÍNH các file kết quả (run_meta.json + calls.jsonl), nên
#  dùng được cả khi mẻ chạy khởi động từ terminal khác, hoặc đã kết thúc.
#
#  Dùng:
#    bash scripts/stage4_status.sh          # chụp trạng thái một lần
#    bash scripts/stage4_status.sh --watch  # tự làm mới mỗi 5s
# ============================================================================
source "$(dirname "${BASH_SOURCE[0]}")/lib_common.sh"

CALL_LOG="$ROOT_DIR/$(yaml_get 'proxy.call_log')"
REPEATS="$(yaml_get 'run.repeats')"
FINDINGS_DIR="$ROOT_DIR/results/findings"

show_status() {
  echo "===== TRẠNG THÁI MẺ CHẠY  ($(date +%H:%M:%S)) ====="
  echo

  # --- Tiến trình đang chạy ---------------------------------------------------
  # Có tiến trình tool nào sống không -> biết mẻ chạy còn hoạt động hay đã dừng.
  local running=""
  if docker ps --format '{{.Image}}' 2>/dev/null | grep -q 'saist'; then
    running="datadog-saist (container Docker)"
  elif tasklist 2>/dev/null | grep -qi 'metis.exe' || pgrep -f 'arm-metis/.venv' >/dev/null 2>&1; then
    running="arm-metis (tiến trình host)"
  fi
  if [[ -n "$running" ]]; then
    c_info "ĐANG CHẠY: $running"
  else
    c_warn "Không thấy tool nào đang chạy (mẻ đã xong, hoặc đang giữa hai lần chạy)."
  fi
  echo

  # --- Bảng các lần chạy đã hoàn tất ------------------------------------------
  printf "%-15s %-14s %-5s %-7s %-7s %-9s %-6s %s\n" \
         "TOOL" "LẦN CHẠY" "EXIT" "GIÂY" "CALL" "FINDINGS" "HỢP LỆ" "GHI CHÚ"
  printf '%.0s-' {1..92}; echo

  local total_done=0
  for tool_dir in "$FINDINGS_DIR"/*/; do
    [[ -d "$tool_dir" ]] || continue
    local tool; tool="$(basename "$tool_dir")"
    [[ "$tool" == "_invalid" ]] && continue
    for run_dir in "$tool_dir"run-*/; do
      [[ -d "$run_dir" ]] || continue
      local run; run="$(basename "$run_dir")"
      local meta="$run_dir/run_meta.json"
      if [[ ! -f "$meta" ]]; then
        printf "%-15s %-14s %-5s %-7s %-7s %-9s %-6s %s\n" \
               "$tool" "$run" "-" "-" "-" "-" "-" "đang chạy..."
        continue
      fi
      total_done=$((total_done + 1))
      # Đọc bằng python cho chắc; jq có thể không có sẵn trên Windows.
      PYTHONIOENCODING=utf-8 python - "$meta" "$run_dir/raw_output.sarif" "$tool" "$run" <<'PY'
import json, os, sys
meta_p, sarif_p, tool, run = sys.argv[1:5]
m = json.load(open(meta_p, encoding="utf-8"))
n = "-"
if os.path.exists(sarif_p):
    try:
        n = len(json.load(open(sarif_p, encoding="utf-8"))["runs"][0].get("results", []))
    except Exception:
        n = "lỗi"
note = []
if m.get("timed_out"):        note.append("TREO-bị giết")
if m.get("rate_limit_hits"):  note.append(f"429x{m['rate_limit_hits']}")
if m.get("model_fallbacks"):  note.append(f"đổi-model x{m['model_fallbacks']}")
valid = m.get("valid")
valid_s = {True: "có", False: "KHÔNG", None: "?"}.get(valid, "?")
print(f"{tool:<15} {run:<14} {m['exit_code']:<5} {m['wall_clock_s']:<7} "
      f"{m['llm_calls']:<7} {str(n):<9} {valid_s:<6} {', '.join(note)}")
PY
    done
  done

  # --- Tổng hợp từ log proxy ---------------------------------------------------
  echo
  if [[ -f "$CALL_LOG" ]]; then
    PYTHONIOENCODING=utf-8 python - "$CALL_LOG" <<'PY'
import json, sys, collections
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
if not rows:
    sys.exit()
cost = sum(r.get("cost_usd") or 0 for r in rows)
tin  = sum(r.get("prompt_tokens") or 0 for r in rows)
tout = sum(r.get("completion_tokens") or 0 for r in rows)
fb   = sum(1 for r in rows if r.get("via_fallback"))
unk  = sum(1 for r in rows if r.get("tool") == "unknown")
print(f"Call LLM   : {len(rows)}   {dict(collections.Counter(r['tool'] for r in rows))}")
print(f"Token      : {tin:,} vào / {tout:,} ra")
print(f"Chi phí    : ${cost:.4f}")
# Hai con số dưới PHẢI là 0; khác 0 là dữ liệu có vấn đề.
flag = lambda v: "  <-- CẦN XEM LẠI" if v else ""
print(f"Qua fallback: {fb}{flag(fb)}")
print(f"Tool unknown: {unk}{flag(unk)}")
PY
  else
    echo "(chưa có $CALL_LOG)"
  fi
  echo
  echo "Đã xong $total_done lần chạy (mục tiêu: $REPEATS lần/tool)."
}

if [[ "${1:-}" == "--watch" ]]; then
  while true; do clear; show_status; sleep 5; done
else
  show_status
fi
