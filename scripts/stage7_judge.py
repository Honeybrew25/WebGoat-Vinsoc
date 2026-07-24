#!/usr/bin/env python3
"""
stage7_judge.py — Giai đoạn 7: chấm precision bằng LLM-as-judge.

Biến `#findings` (đếm thô, dễ gian lận bằng spam) thành `#TP` và `precision`
(TP/(TP+FP)) — con số duy nhất làm cho bảng xếp hạng có nghĩa.

Bốn nguyên tắc, mỗi cái chống một kiểu sai:

  1. CHẤM MÙ. Judge không biết finding đến từ tool nào. Biết tên tool là mở đường
     cho thiên vị mà ta không kiểm chứng được.

  2. ĐỌC `message`, KHÔNG đọc `rule_id_raw`. Đo được ở Giai đoạn 5: 33% finding
     của SAIST có ruleId mâu thuẫn với chính message của nó. Chấm theo nhãn rule
     là chấm sai đối tượng.

  3. ĐƯA CẢ FILE khi file đủ nhỏ. 56% số dòng của Metis là rác (startLine=1).
     Chỉ đưa cửa sổ quanh dòng đó thì judge đọc nhầm chỗ -> chấm oan Metis. Đây
     là chỗ dễ vô tình thiên vị nhất trong cả giai đoạn.

  4. PHẠM VI CHUNG. Metis quét cả .js (65 finding, 43 trong thư viện đóng gói),
     SAIST chỉ quét .java. So thẳng là so phạm vi, không phải so năng lực.

Chạy:
    uv run --with pyyaml python scripts/stage7_judge.py --limit 5   # thử
    uv run --with pyyaml python scripts/stage7_judge.py             # chấm hết
    uv run --with pyyaml python scripts/stage7_judge.py --workers 4 --resume

Đầu ra: results/findings/normalized/judged.jsonl, results/stats/precision.json
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import yaml

sys.stdout.reconfigure(encoding="utf-8", newline="\n")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CFG = os.path.join(_ROOT, "config", "benchmark.yaml")

_VENDOR = re.compile(
    r"(^|/)(node_modules|vendor|libs?|assets|static|webjars)/"
    r"|jquery|bootstrap|ace\.js|underscore|\.min\.(js|css)$",
    re.I,
)

_PROMPT = """You are a strict application-security reviewer auditing a Java/Spring \
web application (OWASP WebGoat, a deliberately vulnerable teaching app).

A static analysis tool reported the finding below. Decide whether it is a REAL \
exploitable vulnerability in this code (TP) or a false alarm (FP).

Judge on the CODE, not on the tool's confidence or wording. Be strict:
- FP if the described data flow does not actually exist in the code shown.
- FP if the "vulnerability" is only a code-quality or robustness issue \
(null checks, resource leaks, style) with no security impact.
- FP if the sink is not reachable from untrusted input.
- TP if untrusted input genuinely reaches a dangerous sink without adequate \
sanitisation, even if this file is intentionally vulnerable teaching code.

REPORTED ISSUE
Title: {title}
Claimed CWE: {cwe}
File: {file}
Claimed line: {line}{line_note}

Tool's reasoning:
{message}

SOURCE CODE ({ctx_desc}):
```java
{code}
```

Answer with ONLY a JSON object, no markdown fence:
{{"verdict":"TP"|"FP","confidence":"high"|"medium"|"low","cwe":"CWE-nnn or null",\
"reason":"<= 30 words"}}"""


class DailyQuotaExceeded(RuntimeError):
    """Quota theo ngày không thể chữa bằng retry trong cùng một mẻ chạy."""


def load_cfg():
    with open(_CFG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def in_scope(rec, scope):
    if scope.get("extensions") and not any(rec["file"].endswith(e) for e in scope["extensions"]):
        return False
    if scope.get("exclude_test_code") and rec.get("in_test_code"):
        return False
    if scope.get("exclude_vendor") and _VENDOR.search(rec["file"]):
        return False
    return True


def build_context(rec, target_dir, jc):
    """
    Lấy code cho judge đọc.

    Trả về (code, mô_tả, ghi_chú_dòng). Ghi chú dòng là chỗ ta THÀNH THẬT với
    judge rằng số dòng có thể sai — thà nói rõ còn hơn để judge tin một con số rác.
    """
    path = os.path.join(target_dir, rec["file"])
    if not os.path.exists(path):
        return None, None, None
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    n = len(lines)
    unreliable = rec.get("line_confidence") != "exact"

    if n <= jc.get("max_whole_file_lines", 400):
        body = "\n".join(f"{i+1}: {l}" for i, l in enumerate(lines))
        desc = f"toàn bộ file, {n} dòng"
    else:
        # File lớn: cắt cửa sổ. Nếu dòng không tin được thì lấy từ đầu file.
        w = jc.get("context_window_lines", 60)
        centre = rec.get("line_min") or 1
        if unreliable:
            centre = 1
        lo = max(0, centre - w // 2)
        hi = min(n, lo + w)
        body = "\n".join(f"{i+1}: {lines[i]}" for i in range(lo, hi))
        desc = f"dòng {lo+1}-{hi} của {n}"

    note = ""
    if unreliable:
        note = ("\nNOTE: the reported line number is UNRELIABLE for this finding — "
                "locate the issue yourself from the reasoning text.")
    return body, desc, note


def build_request_body(prompt, alias, reasoning_effort=None):
    body = {
        "model": alias,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    return body


def is_daily_quota_error(status, body):
    """Phân biệt quota ngày (fail-fast) với RPM/503 tạm thời (retry được)."""
    text = (body or "").lower()
    return status == 429 and (
        "generate_requests_per_model_per_day" in text
        or "generaterequestsperdayperprojectpermodel" in text
        or re.search(r"retry (?:in|after) \d+h", text) is not None
    )


def call_judge(prompt, base_url, key, alias, reasoning_effort=None, retries=6):
    req_body = json.dumps(
        build_request_body(prompt, alias, reasoning_effort)
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=req_body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "x-tool-name": "_judge"},
    )
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.load(resp)
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if is_daily_quota_error(e.code, body):
                raise DailyQuotaExceeded(
                    "quota request/model/ngày đã hết; dừng mẻ và resume sau khi quota reset"
                ) from e
            last = RuntimeError(f"HTTP {e.code}: {body[:500]}")
            time.sleep(min(30, 3 * (attempt + 1)))
        except Exception as e:                       # 503/timeout -> thử lại
            last = e
            # Backoff tăng dần, trần 30s. Model mạnh (pro/3.5) hay 503 khi quá
            # tải; grind qua được nếu 503 chỉ lác đác, nhưng nếu quá tải KÉO DÀI
            # thì càng retry càng chậm — theo dõi bằng scripts/judge_status.sh,
            # thấy "NGHI KẸT" thì dừng và thử lại lúc khác.
            time.sleep(min(30, 3 * (attempt + 1)))
    raise RuntimeError(f"judge call thất bại sau {retries} lần: {last}")


def parse_verdict(text):
    """Model đôi khi bọc JSON trong ```json … ``` dù đã dặn đừng."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def record_key(rec):
    """Khoá ổn định dùng để checkpoint/resume và đối chiếu hai judge."""
    return (rec["tool"], rec["file"], rec.get("line_min"), rec["title"])


def load_checkpoint(path):
    """Đọc các phán quyết hợp lệ đã ghi; dòng trùng giữ bản ghi cuối."""
    completed = {}
    if not os.path.exists(path):
        return completed
    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"checkpoint hỏng ở dòng {line_no}: {exc}") from exc
            if rec.get("verdict") not in ("TP", "FP"):
                raise ValueError(f"checkpoint dòng {line_no} thiếu verdict TP/FP")
            completed[record_key(rec)] = rec
    return completed


def remaining_records(records, completed):
    """Giữ nguyên thứ tự nguồn và bỏ các finding đã có trong checkpoint."""
    return [rec for rec in records if record_key(rec) not in completed]


def map_records(records, work, workers=1):
    """Chạy độc lập từng finding và yield ngay task nào hoàn tất trước."""
    if workers < 1:
        raise ValueError("workers phải >= 1")
    if workers == 1:
        yield from map(work, records)
        return
    iterator = iter(records)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    pending = set()
    try:
        for _ in range(workers):
            try:
                pending.add(executor.submit(work, next(iterator)))
            except StopIteration:
                break
        while pending:
            done, pending = concurrent.futures.wait(
                pending, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                yield future.result()
                try:
                    pending.add(executor.submit(work, next(iterator)))
                except StopIteration:
                    pass
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)


def judge_record(rec, target_dir, jc, base_url, key, alias,
                 reasoning_effort=None, retries=6):
    """Chấm một finding; trả về (record_đã_chấm, lỗi)."""
    code, desc, note = build_context(rec, target_dir, jc)
    if code is None:
        return None, f"không đọc được file: {rec['file']}"
    prompt = _PROMPT.format(
        title=rec["title"], cwe=rec.get("cwe") or "unknown",
        file=rec["file"], line=rec.get("line_min"),
        line_note=note, message=rec["message"], code=code, ctx_desc=desc,
    )
    try:
        verdict = parse_verdict(call_judge(
            prompt, base_url, key, alias, reasoning_effort=reasoning_effort,
            retries=retries,
        ))
    except DailyQuotaExceeded:
        raise
    except Exception as exc:
        return None, str(exc)
    if not verdict or verdict.get("verdict") not in ("TP", "FP"):
        return None, "judge trả verdict không hợp lệ"

    result = dict(rec)
    result["verdict"] = verdict["verdict"]
    result["judge_confidence"] = verdict.get("confidence")
    result["judge_cwe"] = verdict.get("cwe")
    result["judge_reason"] = (verdict.get("reason") or "")[:300]
    result["judge_alias"] = alias
    result["judge_reasoning_effort"] = reasoning_effort or "default"
    return result, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="chỉ chấm N finding đầu (để thử)")
    ap.add_argument("--all-scope", action="store_true",
                    help="chấm cả finding ngoài phạm vi chung (mặc định: chỉ phạm vi chung)")
    ap.add_argument("--judge-alias", default=None,
                    help="ghi đè alias judge (vd gemini-31-pro-judge cho judge độc lập)")
    ap.add_argument("--out-suffix", default="",
                    help="hậu tố tên file đầu ra (vd '-pro' -> judged-pro.jsonl)")
    ap.add_argument("--workers", type=int, default=1,
                    help="số call judge chạy đồng thời (mặc định: 1)")
    ap.add_argument("--resume", action="store_true",
                    help="đọc output hiện có và chỉ chấm các finding còn thiếu")
    ap.add_argument("--reasoning-effort", choices=("low", "medium", "high"),
                    default=None,
                    help="mức suy luận OpenAI-compatible; Gemini ánh xạ sang thinking_level")
    ap.add_argument("--retries", type=int, default=6,
                    help="số lần thử tối đa ở script cho mỗi finding (mặc định: 6)")
    args = ap.parse_args()
    if args.workers < 1:
        ap.error("--workers phải >= 1")
    if args.retries < 1:
        ap.error("--retries phải >= 1")

    cfg = load_cfg()
    jc = cfg["judge"]
    scope = jc.get("scope", {})
    base_url = cfg["proxy"]["base_url"]
    target_dir = os.path.join(_ROOT, cfg["target"]["local_path"])
    alias = args.judge_alias or jc["model_alias"]

    env = {}
    with open(os.path.join(_ROOT, "proxy", ".env"), encoding="utf-8") as fh:
        for line in fh:
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v
    key = env.get("LITELLM_MASTER_KEY", "")

    src = os.path.join(_ROOT, "results", "findings", "normalized", "deduped.jsonl")
    recs = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]

    for r in recs:
        r["in_scope"] = in_scope(r, scope)
    todo = recs if args.all_scope else [r for r in recs if r["in_scope"]]
    if args.limit:
        todo = todo[:args.limit]

    print(f"Chấm {len(todo)} finding "
          f"(tổng {len(recs)}, trong phạm vi chung {sum(1 for r in recs if r['in_scope'])})")
    effort = args.reasoning_effort or "model-default"
    print(f"Alias judge: {alias} | chấm mù: {jc.get('blind')} | "
          f"workers: {args.workers} | reasoning: {effort} | retries: {args.retries}")
    print()

    out_path = os.path.join(_ROOT, "results", "findings", "normalized",
                            f"judged{args.out_suffix}.jsonl")
    valid_keys = {record_key(rec) for rec in todo}
    checkpoint = load_checkpoint(out_path) if args.resume else {}
    checkpoint = {k: v for k, v in checkpoint.items() if k in valid_keys}
    pending = remaining_records(todo, checkpoint)
    if args.resume:
        print(f"Resume: đã có {len(checkpoint)}/{len(todo)}, còn {len(pending)} finding")

    judged_by_key = dict(checkpoint)
    mode = "a" if args.resume else "w"
    work = lambda rec: judge_record(
        rec, target_dir, jc, base_url, key, alias,
        args.reasoning_effort, args.retries,
    )
    with open(out_path, mode, encoding="utf-8", newline="\n") as out:
        for processed, (result, error) in enumerate(
                map_records(pending, work, args.workers), 1):
            position = len(checkpoint) + processed
            if error:
                print(f"  [{position}] LỖI: {error}")
            else:
                judged_by_key[record_key(result)] = result
                out.write(json.dumps(result, ensure_ascii=False) + "\n")
                out.flush()  # kill tiến trình vẫn giữ checkpoint đã hoàn tất
            if position % 10 == 0 or position == len(todo):
                judged_now = list(judged_by_key.values())
                tp = sum(1 for r in judged_now if r["verdict"] == "TP")
                failed_now = position - len(judged_now)
                print(f"  {position}/{len(todo)} — TP {tp}, "
                      f"FP {len(judged_now)-tp}, lỗi {failed_now}", flush=True)

    # Khi chạy xong, chuẩn hoá thứ tự và loại mọi dòng trùng trong checkpoint cũ.
    judged = [judged_by_key[record_key(rec)] for rec in todo
              if record_key(rec) in judged_by_key]
    with open(out_path, "w", encoding="utf-8", newline="\n") as out:
        for rec in judged:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    failed = len(todo) - len(judged)

    # --- Thống kê -------------------------------------------------------------
    import collections
    stats = {}
    for tool in sorted({r["tool"] for r in judged}):
        rs = [r for r in judged if r["tool"] == tool]
        tp = sum(1 for r in rs if r["verdict"] == "TP")
        fp = len(rs) - tp
        stable = [r for r in rs if r["run_count"] >= cfg["dedup"]["stable_min_runs"]]
        tp_s = sum(1 for r in stable if r["verdict"] == "TP")
        stats[tool] = {
            "judged": len(rs), "tp": tp, "fp": fp,
            "precision": round(tp / len(rs), 4) if rs else None,
            "stable_judged": len(stable), "stable_tp": tp_s,
            "stable_precision": round(tp_s / len(stable), 4) if stable else None,
        }

    print()
    print(f"{'TOOL':<15}{'CHẤM':>6}{'TP':>5}{'FP':>5}{'PRECISION':>11}{'TP (3/3 run)':>14}{'PREC ỔN ĐỊNH':>14}")
    print("-" * 71)
    for tool, s in stats.items():
        sp = f"{s['stable_precision']*100:.1f}%" if s["stable_precision"] is not None else "-"
        print(f"{tool:<15}{s['judged']:>6}{s['tp']:>5}{s['fp']:>5}"
              f"{s['precision']*100:>10.1f}%{s['stable_tp']:>14}{sp:>14}")
    if failed:
        print(f"\nKhông chấm được: {failed}")

    prec_path = os.path.join(_ROOT, "results", "stats", f"precision{args.out_suffix}.json")
    with open(prec_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"scope": scope, "blind": jc.get("blind"),
                   "judge_alias": alias, "per_tool": stats,
                   "reasoning_effort": args.reasoning_effort or "model-default",
                   "retry_budget": args.retries,
                   "failed": failed}, fh, ensure_ascii=False, indent=2)
    print(f"\n-> {os.path.relpath(out_path, _ROOT)}")
    print(f"-> {os.path.relpath(prec_path, _ROOT)}")


if __name__ == "__main__":
    main()
