#!/usr/bin/env bash
# ============================================================================
#  judge_status.sh — theo dõi tiến độ LLM-as-judge trực tiếp
# ============================================================================
#  Đọc từ proxy log (results/proxy_logs/calls.jsonl), nên thấy được cả khi judge
#  chạy ở terminal/tiến trình khác. Lọc theo model để tách judge yếu vs judge mạnh.
#
#  Dùng:
#    bash scripts/judge_status.sh                       # chụp một lần
#    bash scripts/judge_status.sh --watch               # tự làm mới 5s
#    bash scripts/judge_status.sh gemini-3.1-pro-preview # chỉ 1 model judge
# ============================================================================
source "$(dirname "${BASH_SOURCE[0]}")/lib_common.sh"

CALL_LOG="$ROOT_DIR/$(yaml_get 'proxy.call_log')"
WATCH=0; MODEL=""
for a in "$@"; do
  case "$a" in
    --watch) WATCH=1 ;;
    *) MODEL="$a" ;;
  esac
done

snapshot() {
  MODEL="$MODEL" TOTAL=238 uv run --quiet python - "$CALL_LOG" <<'PY'
import json, os, sys, datetime
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
log = sys.argv[1]
model = os.environ.get("MODEL", "")
total = int(os.environ.get("TOTAL", "238"))
rows = [json.loads(l) for l in open(log, encoding="utf-8") if l.strip()]
j = [r for r in rows if r.get("tool") == "_judge"]
if model:
    j = [r for r in j if r.get("model") == model]

print(f"=== JUDGE STATUS  {datetime.datetime.now():%H:%M:%S} ===")
if not j:
    print("Chưa có call judge nào" + (f" cho model {model}" if model else ""))
    raise SystemExit
# Tách theo model để không trộn judge yếu và judge mạnh.
import collections
by_model = collections.defaultdict(list)
for r in j:
    by_model[r.get("model")].append(r)
for m, rs in by_model.items():
    n = len(rs)
    cost = sum(r.get("cost_usd") or 0 for r in rs)
    ts = [datetime.datetime.fromisoformat(r["ts"]) for r in rs]
    now = datetime.datetime.now(datetime.timezone.utc)
    since = (now - max(ts)).total_seconds()
    bar_n = int(n / total * 30)
    bar = "#" * bar_n + "." * (30 - bar_n)
    print(f"\n{m}")
    print(f"  [{bar}] {n}/{total} = {n/total*100:.0f}%")
    print(f"  chi phí ${cost:.3f}   |   call gần nhất cách đây {since:.0f}s", end="")
    if since > 90:
        print("  <-- NGHI KẸT (>90s không có call mới)")
    else:
        print()
    if n >= 2:
        span = (max(ts) - min(ts)).total_seconds()
        rate = span / (n - 1)
        print(f"  tốc độ {rate:.0f}s/call   |   còn lại ~{(total-n)*rate/60:.0f} phút")
PY
}

if [[ $WATCH -eq 1 ]]; then
  while true; do clear; snapshot; sleep 5; done
else
  snapshot
fi
