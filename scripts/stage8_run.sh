#!/usr/bin/env bash
# Stage 8: run the approved Pareto profile without touching Stage 4-7 outputs.
source "$(dirname "${BASH_SOURCE[0]}")/lib_common.sh"

PROFILE="$(bench_cfg get stage8.active_profile)"
MODE=""
ONLY_TOOL=""

usage() {
  cat <<'EOF'
Usage:
  bash scripts/stage8_run.sh --dry-run [--profile NAME] [--tool TOOL]
  bash scripts/stage8_run.sh --screen  [--profile NAME] [--tool TOOL]
  bash scripts/stage8_run.sh --complete [--profile NAME] [--tool TOOL]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|--screen|--complete)
      [[ -z "$MODE" ]] || { c_err "Chỉ chọn một mode."; exit 1; }
      MODE="${1#--}"
      shift
      ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --tool) ONLY_TOOL="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) c_err "Tham số lạ: $1"; usage; exit 1 ;;
  esac
done

[[ -n "$MODE" ]] || { c_err "Thiếu mode --dry-run, --screen hoặc --complete."; exit 1; }
[[ "$PROFILE" =~ ^[A-Za-z0-9._-]+$ ]] \
  || { c_err "Tên profile không hợp lệ: $PROFILE"; exit 1; }

PROFILE_ROOT="$ROOT_DIR/results/optimization/$PROFILE"
OUT_ROOT="$PROFILE_ROOT/runs"
case "$OUT_ROOT/" in
  "$ROOT_DIR/results/optimization/"*) ;;
  *) c_err "Stage 8 output escaped results/optimization"; exit 1 ;;
esac
export BENCH_RESULTS_ROOT="$OUT_ROOT"

PROFILE_TOOLS="$(bench_cfg stage8-tools "$PROFILE")"
if [[ -n "$ONLY_TOOL" ]] && ! grep -qxF "$ONLY_TOOL" <<<"$PROFILE_TOOLS"; then
  c_err "Tool '$ONLY_TOOL' không thuộc profile '$PROFILE'."
  exit 1
fi

selected_tools() {
  if [[ -n "$ONLY_TOOL" ]]; then
    printf '%s\n' "$ONLY_TOOL"
  else
    printf '%s\n' "$PROFILE_TOOLS"
  fi
}

profile_value() {
  local tool="$1" key="$2" payload
  payload="$(bench_cfg stage8-profile "$PROFILE" "$tool")"
  STAGE8_PROFILE_JSON="$payload" STAGE8_PROFILE_KEY="$key" \
    uv run --quiet python -c '
import json, os
value = json.loads(os.environ["STAGE8_PROFILE_JSON"])[os.environ["STAGE8_PROFILE_KEY"]]
print("\n".join(map(str, value)) if isinstance(value, list) else value)
' | tr -d '\r'
}

# Values always come from benchmark.yaml; adapters remain unchanged when these
# variables are absent outside Stage 8.
export METIS_MAX_WORKERS="$(profile_value arm-metis max_workers)"
export METIS_REVIEW_INCLUDE="$(profile_value arm-metis review_include)"
export METIS_REVIEW_EXCLUDES="$(profile_value arm-metis review_exclude)"
export SAIST_FILE_CONCURRENCY="$(profile_value datadog-saist file_concurrency)"

evaluate() {
  uv run --quiet --with pyyaml python "$ROOT_DIR/scripts/stage8_evaluate.py" \
    --profile "$PROFILE" "$@"
}

write_profile_manifest() {
  uv run --quiet --with pyyaml python - \
    "$ROOT_DIR" "$PROFILE" "$PROFILE_ROOT/profile.json" <<'PY'
import datetime as dt
import json
import pathlib
import sys
import yaml

root_arg, profile, output = sys.argv[1:]
root = pathlib.Path(root_arg)
cfg = yaml.safe_load((root / "config" / "benchmark.yaml").read_text(encoding="utf-8"))
value = {
    "profile": profile,
    "target_sha": cfg["target"]["sha"],
    "model": cfg["model"],
    "budget_usd": cfg["stage8"]["budget_usd"],
    "judge_reserve_usd": cfg["stage8"]["judge_reserve_usd"],
    "knobs": cfg["stage8"]["profiles"][profile],
    "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
}
path = pathlib.Path(output)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8", newline="\n")
PY
}

run_index() {
  local tool="$1" index="$2" phase="warm" run_dir
  [[ "$index" -eq 1 ]] && phase="cold"
  run_dir="$OUT_ROOT/$tool/run-$(printf '%02d' "$index")-$phase"
  [[ ! -e "$run_dir" ]] \
    || { c_err "Run đã tồn tại, không tự ghi đè: $run_dir"; return 1; }

  evaluate --budget-check --tool "$tool" --next-run "$index"
  bash "$ROOT_DIR/scripts/stage4_run.sh" \
    --tool "$tool" --run-index "$index" --output-root "$OUT_ROOT"
  uv run --quiet python - "$run_dir/run_meta.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.exists():
    raise SystemExit(f"missing run manifest: {path}")
meta = json.loads(path.read_text(encoding="utf-8"))
if meta.get("valid") is not True:
    raise SystemExit(f"invalid Stage 8 run: {path.parent}")
PY
}

dry_run() {
  c_ok "Stage 8 dry-run — không gọi adapter/API."
  c_info "Profile: $PROFILE"
  c_info "Output: $OUT_ROOT"
  c_info "Hard cap: \$$(bench_cfg get stage8.budget_usd); judge reserve: \$$(bench_cfg get stage8.judge_reserve_usd)"
  printf '    METIS_MAX_WORKERS=%s\n' "$METIS_MAX_WORKERS"
  printf '    METIS_REVIEW_INCLUDE=%s\n' "$METIS_REVIEW_INCLUDE"
  printf '    METIS_REVIEW_EXCLUDES=%q\n' "$METIS_REVIEW_EXCLUDES"
  printf '    SAIST_FILE_CONCURRENCY=%s\n' "$SAIST_FILE_CONCURRENCY"

  local tool index check projection total
  for tool in $(selected_tools); do
    total="0"
    c_info "Kế hoạch $tool:"
    for index in 1 2 3; do
      check="$(evaluate --budget-check --tool "$tool" --next-run "$index")"
      projection="$(
        STAGE8_CHECK_JSON="$check" uv run --quiet python -c \
          'import json,os; print(json.loads(os.environ["STAGE8_CHECK_JSON"])["projected_run_usd"])'
      )"
      total="$(
        uv run --quiet python -c \
          "print(round(float('$total') + float('$projection'), 6))"
      )"
      printf '    run %s projected: $%s\n' "$index" "$projection"
      printf '    bash scripts/stage4_run.sh --tool %s --run-index %s --output-root %q\n' \
        "$tool" "$index" "$OUT_ROOT"
    done
    printf '    projected 3-run subtotal: $%s\n' "$total"
  done
}

case "$MODE" in
  dry-run)
    dry_run
    ;;
  screen)
    write_profile_manifest
    for tool in $(selected_tools); do
      run_index "$tool" 1
    done
    evaluate --mode screening
    ;;
  complete)
    if [[ -n "$ONLY_TOOL" ]]; then
      passing="$(evaluate --require-screening-pass --tool "$ONLY_TOOL")"
    else
      passing="$(evaluate --require-screening-pass)"
    fi
    write_profile_manifest
    for tool in $passing; do
      run_index "$tool" 2
      run_index "$tool" 3
    done
    evaluate --prepare-novel-judge
    ;;
esac
