#!/usr/bin/env python3
"""Submit/monitor/collect the independent judge through Gemini Batch API.

The interactive endpoint is unsuitable for this evaluation: Gemini 3.1 Pro hit
its 250 requests/day quota and Gemini 3 Flash intermittently returns 503. Batch
uses a separate quota, preserves input order, and costs 50% of standard calls.

Usage:
    uv run --with pyyaml python scripts/stage7_batch_judge.py --submit
    uv run --with pyyaml python scripts/stage7_batch_judge.py --status
    uv run --with pyyaml python scripts/stage7_batch_judge.py --collect

Use --source, --out, and --state together to judge a repository-confined
alternative input without touching the Stage 7 artifacts.
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", newline="\n")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import stage7_judge as judge  # noqa: E402

_DEFAULT_STATE = os.path.join(
    _ROOT, "results", "stats", "judge_batch_state.json"
)
_DEFAULT_SOURCE = os.path.join(
    _ROOT, "results", "findings", "normalized", "deduped.jsonl"
)
_DEFAULT_OUT = os.path.join(
    _ROOT, "results", "findings", "normalized", "judged-independent.jsonl"
)
_API = "https://generativelanguage.googleapis.com/v1beta"
# Gemini Developer API paid-tier Batch prices, USD per one million tokens.
# Source checked 2026-07-23: https://ai.google.dev/gemini-api/docs/pricing
_BATCH_PRICING = {
    "gemini-3-flash-preview": {
        "input_per_million": 0.25,
        "output_per_million": 1.50,
    },
}
_TERMINAL = {
    "JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
    "BATCH_STATE_SUCCEEDED", "BATCH_STATE_FAILED", "BATCH_STATE_CANCELLED",
    "BATCH_STATE_EXPIRED",
}


def build_inline_request(rec, target_dir, jc, effort="low"):
    code, desc, note = judge.build_context(rec, target_dir, jc)
    if code is None:
        raise FileNotFoundError(rec["file"])
    prompt = judge._PROMPT.format(
        title=rec["title"], cwe=rec.get("cwe") or "unknown",
        file=rec["file"], line=rec.get("line_min"), line_note=note,
        message=rec["message"], code=code, ctx_desc=desc,
    )
    return {
        "request": {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generation_config": {
                "thinking_config": {"thinking_level": effort},
            },
        },
        "metadata": {"key": list(judge.record_key(rec))},
    }


def _first(mapping, *names):
    for name in names:
        if isinstance(mapping, dict) and name in mapping:
            return mapping[name]
    return None


def batch_resource(operation):
    """Return batch metadata, where state and counters live in the real API."""
    return (
        _first(operation, "metadata")
        or _first(operation, "response")
        or operation
    )


def batch_state(operation):
    return _first(batch_resource(operation), "state")


def batch_output(operation):
    """Unwrap inline results from either completed-LRO response shape."""
    response = _first(operation, "response") or {}
    if _first(response, "inlinedResponses", "inlined_responses") is not None:
        return response
    resource = batch_resource(operation)
    return _first(resource, "output") or response


def _response_text(item):
    response = _first(item, "response") or {}
    candidates = _first(response, "candidates") or []
    if not candidates:
        return None
    content = _first(candidates[0], "content") or {}
    parts = _first(content, "parts") or []
    texts = [part.get("text") for part in parts if part.get("text")]
    return texts[-1] if texts else None


def apply_batch_responses(pending, responses, completed, alias, effort):
    if len(responses) != len(pending):
        raise ValueError(
            f"batch trả {len(responses)} response cho {len(pending)} request"
        )
    merged = dict(completed)
    errors = []
    for rec, item in zip(pending, responses):
        item_error = _first(item, "error")
        if item_error:
            errors.append(f"{rec['title']}: {item_error.get('message', item_error)}")
            continue
        verdict = judge.parse_verdict(_response_text(item))
        if not verdict or verdict.get("verdict") not in ("TP", "FP"):
            errors.append(f"{rec['title']}: verdict không hợp lệ")
            continue
        result = dict(rec)
        result["verdict"] = verdict["verdict"]
        result["judge_confidence"] = verdict.get("confidence")
        result["judge_cwe"] = verdict.get("cwe")
        result["judge_reason"] = (verdict.get("reason") or "")[:300]
        result["judge_alias"] = alias
        result["judge_reasoning_effort"] = effort
        result["judge_transport"] = "gemini-batch"
        merged[judge.record_key(result)] = result
    return merged, errors


def summarize_batch_usage(responses, model):
    if model not in _BATCH_PRICING:
        raise ValueError(f"missing Batch API pricing for model: {model}")
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    missing_usage = 0
    billed_items = 0
    for item in responses:
        if _first(item, "error"):
            continue
        billed_items += 1
        response = _first(item, "response") or {}
        usage = _first(response, "usageMetadata", "usage_metadata")
        if not usage:
            missing_usage += 1
            continue
        prompt = int(_first(
            usage, "promptTokenCount", "prompt_token_count"
        ) or 0)
        candidates = int(_first(
            usage, "candidatesTokenCount", "candidates_token_count"
        ) or 0)
        thoughts = int(_first(
            usage, "thoughtsTokenCount", "thoughts_token_count"
        ) or 0)
        total = int(_first(
            usage, "totalTokenCount", "total_token_count"
        ) or (prompt + candidates + thoughts))
        input_tokens += prompt
        output_tokens += candidates + thoughts
        total_tokens += total

    pricing = _BATCH_PRICING[model]
    estimated_cost = (
        input_tokens * pricing["input_per_million"]
        + output_tokens * pricing["output_per_million"]
    ) / 1_000_000
    return {
        "model": model,
        "billed_items": billed_items,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "usage_complete": missing_usage == 0,
        "missing_usage_items": missing_usage,
        "pricing_usd_per_million": pricing,
        "estimated_cost_usd": round(estimated_cost, 9),
    }


def _env_key():
    env = {}
    with open(os.path.join(_ROOT, "proxy", ".env"), encoding="utf-8") as fh:
        for line in fh:
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.strip().split("=", 1)
                env[key] = value
    api_key = env.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY đang trống")
    return api_key


def _http_json(method, url, body=None, timeout=180):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Batch API HTTP {exc.code}: {detail[:1000]}") from exc


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _repo_path(value, default):
    path = (
        os.path.abspath(os.path.join(_ROOT, value))
        if value
        else os.path.abspath(default)
    )
    root = os.path.abspath(_ROOT)
    try:
        confined = os.path.normcase(os.path.commonpath([root, path]))
    except ValueError:
        confined = ""
    if confined != os.path.normcase(root):
        raise ValueError(f"path escapes repository: {value}")
    return path


def resolve_paths(source=None, out=None, state=None):
    return (
        _repo_path(source, _DEFAULT_SOURCE),
        _repo_path(out, _DEFAULT_OUT),
        _repo_path(state, _DEFAULT_STATE),
    )


def _load_state(state_path):
    if not os.path.exists(state_path):
        raise RuntimeError("chưa có batch state; chạy --submit trước")
    with open(state_path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_work(source_path, out_path):
    cfg = judge.load_cfg()
    jc = cfg["judge"]
    target_dir = os.path.join(_ROOT, cfg["target"]["local_path"])
    recs = [
        json.loads(line)
        for line in open(source_path, encoding="utf-8")
        if line.strip()
    ]
    for rec in recs:
        rec["in_scope"] = judge.in_scope(rec, jc["scope"])
    todo = [rec for rec in recs if rec["in_scope"]]
    completed = judge.load_checkpoint(out_path)
    return cfg, jc, target_dir, todo, completed


def submit(model, alias, effort, source_path, out_path, state_path):
    if os.path.exists(state_path):
        state = _load_state(state_path)
        if not state.get("collected"):
            raise RuntimeError(
                f"batch {state.get('job_name')} đã tồn tại; dùng --status, không submit lại"
            )
    _, jc, target_dir, todo, completed = _load_work(source_path, out_path)
    pending = judge.remaining_records(todo, completed)
    if not pending:
        raise RuntimeError("không có finding nào cần submit")
    requests = [build_inline_request(rec, target_dir, jc, effort) for rec in pending]
    payload = {
        "batch": {
            "display_name": f"webgoat-independent-judge-{dt.datetime.now():%Y%m%d-%H%M%S}",
            "input_config": {"requests": {"requests": requests}},
        }
    }
    encoded_size = len(json.dumps(payload).encode("utf-8"))
    if encoded_size >= 20 * 1024 * 1024:
        raise RuntimeError(f"inline batch {encoded_size} byte vượt giới hạn 20MB")
    api_key = _env_key()
    url = f"{_API}/models/{model}:batchGenerateContent?key={api_key}"
    job = _http_json("POST", url, payload)
    name = _first(job, "name")
    if not name:
        raise RuntimeError(f"Batch API không trả job name: {job}")
    state = {
        "job_name": name,
        "model": model,
        "alias": alias,
        "effort": effort,
        "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "request_count": len(pending),
        "payload_bytes": encoded_size,
        "pending_keys": [list(judge.record_key(rec)) for rec in pending],
        "last_state": _first(job, "state"),
        "collected": False,
    }
    _write_json(state_path, state)
    print(f"Đã submit {len(pending)} finding ({encoded_size/1e6:.2f} MB): {name}")
    print(f"State: {os.path.relpath(state_path, _ROOT)}")


def fetch_job(state):
    api_key = _env_key()
    return _http_json("GET", f"{_API}/{state['job_name']}?key={api_key}")


def status(state_path):
    state = _load_state(state_path)
    job = fetch_job(state)
    resource = batch_resource(job)
    current = batch_state(job)
    stats = _first(resource, "batchStats", "batch_stats") or {}
    state["last_state"] = current
    state["batch_stats"] = stats
    state["checked_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _write_json(state_path, state)
    print(f"{state['job_name']}: {current}")
    if stats:
        print(json.dumps(stats, ensure_ascii=False))
    return current


def collect(source_path, out_path, state_path):
    state = _load_state(state_path)
    job = fetch_job(state)
    current = batch_state(job)
    if current not in ("JOB_STATE_SUCCEEDED", "BATCH_STATE_SUCCEEDED"):
        raise RuntimeError(f"batch chưa thành công: {current}")
    output = batch_output(job)
    inline = _first(output, "inlinedResponses", "inlined_responses") or {}
    responses = _first(inline, "inlinedResponses", "inlined_responses")
    if responses is None:
        raise RuntimeError("batch thành công nhưng không có inlined responses")

    _, _, _, todo, completed = _load_work(source_path, out_path)
    todo_by_key = {judge.record_key(rec): rec for rec in todo}
    pending = [todo_by_key[tuple(key)] for key in state["pending_keys"]]
    merged, errors = apply_batch_responses(
        pending, responses, completed, state["alias"], state["effort"]
    )
    usage = summarize_batch_usage(responses, state["model"])
    ordered = [merged[judge.record_key(rec)] for rec in todo
               if judge.record_key(rec) in merged]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        for rec in ordered:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    state["collected"] = True
    state["collected_count"] = len(ordered)
    state["item_errors"] = errors
    state["last_state"] = current
    state["usage"] = usage
    state["estimated_cost_usd"] = usage["estimated_cost_usd"]
    _write_json(state_path, state)
    print(f"Đã collect {len(ordered)}/{len(todo)} finding; lỗi item: {len(errors)}")
    print(f"-> {os.path.relpath(out_path, _ROOT)}")
    for error in errors[:10]:
        print(f"  LỖI: {error}")


def main():
    ap = argparse.ArgumentParser()
    action = ap.add_mutually_exclusive_group(required=True)
    action.add_argument("--submit", action="store_true")
    action.add_argument("--status", action="store_true")
    action.add_argument("--collect", action="store_true")
    ap.add_argument("--model", default="gemini-3-flash-preview")
    ap.add_argument("--alias", default="gemini-3-flash-judge")
    ap.add_argument("--reasoning-effort", default="low", choices=("low", "medium", "high"))
    ap.add_argument("--source")
    ap.add_argument("--out")
    ap.add_argument("--state")
    args = ap.parse_args()
    source_path, out_path, state_path = resolve_paths(
        args.source, args.out, args.state
    )
    if args.submit:
        submit(
            args.model,
            args.alias,
            args.reasoning_effort,
            source_path,
            out_path,
            state_path,
        )
    elif args.status:
        status(state_path)
    else:
        collect(source_path, out_path, state_path)


if __name__ == "__main__":
    main()
