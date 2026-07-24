#!/usr/bin/env python3
"""
stage6_dedup.py — Giai đoạn 6: gộp finding trùng rồi đếm.

Ba tầng gộp, mỗi tầng trả lời một câu hỏi khác nhau:

  1. TRONG một lần chạy  -> "tool này thực sự báo bao nhiêu vấn đề riêng biệt?"
     (Metis báo 278 kết quả nhưng chỉ ~262 duy nhất — nó tự trùng với chính mình)

  2. GIỮA các lần chạy   -> "vấn đề nào lặp lại được, vấn đề nào chỉ là nhiễu?"
     Finding chỉ xuất hiện 1/3 lần chạy là bằng chứng YẾU. Vẫn đếm, nhưng phải
     tách riêng — nếu không, tool nào ngẫu nhiên hơn sẽ được thưởng oan bằng
     cách cộng dồn nhiễu qua nhiều lần chạy.

  3. GIỮA hai tool       -> "hai tool có nhìn thấy cùng những vấn đề không?"
     Phần giao là ứng viên True Positive mạnh nhất cho Giai đoạn 7.

Chạy:
    uv run --with pyyaml python scripts/stage6_dedup.py

Đầu ra:
    results/findings/normalized/deduped.jsonl  — mỗi dòng 1 vấn đề duy nhất/tool
    results/stats/counts.json                  — bảng đếm đầy đủ
"""
import collections
import json
import os
import re
import sys

import yaml

sys.stdout.reconfigure(encoding="utf-8", newline="\n")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CFG = os.path.join(_ROOT, "config", "benchmark.yaml")


def load_cfg():
    with open(_CFG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --- Chuẩn hoá tiêu đề để so khớp -------------------------------------------
#  Hai tool mô tả cùng một lỗ hổng bằng chữ khác nhau:
#     SAIST: "datadog/java-sqli"
#     Metis: "SQL Injection"
#  Nên KHÔNG so tiêu đề thô. Trong-tool thì so tiêu đề (đủ dùng vì cùng cách viết);
#  giữa-tool thì bắt buộc so bằng CWE.
_WORD = re.compile(r"[^a-z0-9]+")


def norm_title(t: str) -> str:
    return _WORD.sub(" ", (t or "").lower()).strip()


def cluster_by_line(items, tolerance):
    """
    Gom các finding cùng (file, title) thành cụm theo số dòng gần nhau.

    Sắp theo dòng rồi quét tuyến tính: nếu dòng hiện tại cách ĐẦU cụm <= tolerance
    thì nhập cụm. Neo vào đầu cụm (không phải phần tử trước) để tránh hiệu ứng
    "chuỗi dây": 1,11,21,31... lần lượt cách nhau 10 sẽ bị gộp hết thành một cụm
    dù hai đầu cách nhau 30 dòng.
    """
    known = sorted([i for i in items if i["start_line"] is not None],
                   key=lambda r: r["start_line"])
    unknown = [i for i in items if i["start_line"] is None]
    clusters = []
    for it in known:
        if clusters and it["start_line"] - clusters[-1][0]["start_line"] <= tolerance:
            clusters[-1].append(it)
        else:
            clusters.append([it])
    if unknown:
        clusters.append(unknown)
    return clusters


def dedup_tool(rows, tolerance):
    """Gộp trong-tool: cùng file + cùng tiêu đề + dòng gần nhau = MỘT vấn đề."""
    buckets = collections.defaultdict(list)
    for r in rows:
        buckets[(r["file"], norm_title(r["title"]))].append(r)

    out = []
    for (file, title), items in buckets.items():
        for cl in cluster_by_line(items, tolerance):
            runs = sorted({i["run"] for i in cl})
            lines = [i["start_line"] for i in cl if i["start_line"] is not None]
            rep = cl[0]
            # CWE: lấy giá trị phổ biến nhất trong cụm, ưu tiên cái có confidence tốt.
            cwes = [i["cwe"] for i in cl if i["cwe"]]
            cwe = collections.Counter(cwes).most_common(1)[0][0] if cwes else None
            out.append({
                "tool": rep["tool"],
                "file": file,
                "title": rep["title"],
                "cwe": cwe,
                "cwe_confidence": rep["cwe_confidence"],
                "line_min": min(lines) if lines else None,
                "line_max": max(lines) if lines else None,
                "line_confidence": rep["line_confidence"],
                "severity": rep["severity"],
                "in_test_code": rep["in_test_code"],
                "runs_seen": runs,
                "run_count": len(runs),
                "occurrences": len(cl),
                "message": rep["message"][:600],
            })
    return out


def cross_tool_overlap(deduped, tolerance):
    """
    Tìm vấn đề mà CẢ HAI tool cùng thấy.

    Khoá: (file, CWE). KHÔNG dùng tiêu đề vì hai tool viết khác nhau hoàn toàn.
    Dòng chỉ dùng để lọc thêm KHI cả hai bên đều có dòng tin được — Metis có 56%
    dòng rác, ép khớp dòng sẽ loại oan gần hết phần giao.
    """
    by_tool = collections.defaultdict(list)
    for d in deduped:
        by_tool[d["tool"]].append(d)
    tools = sorted(by_tool)
    if len(tools) < 2:
        return [], tools

    a, b = tools[0], tools[1]
    matches = []
    for x in by_tool[a]:
        if not x["cwe"]:
            continue
        for y in by_tool[b]:
            if y["cwe"] != x["cwe"] or y["file"] != x["file"]:
                continue
            # Chỉ ép khớp dòng khi CẢ HAI đều có dòng đáng tin.
            if (x["line_confidence"] == "exact" and y["line_confidence"] == "exact"
                    and x["line_min"] is not None and y["line_min"] is not None):
                if abs(x["line_min"] - y["line_min"]) > tolerance:
                    continue
            matches.append({
                "file": x["file"], "cwe": x["cwe"],
                a: {"title": x["title"], "line": x["line_min"], "run_count": x["run_count"]},
                b: {"title": y["title"], "line": y["line_min"], "run_count": y["run_count"]},
            })
    return matches, tools


def main():
    cfg = load_cfg()
    tol = cfg.get("dedup", {}).get("line_tolerance", 10)
    stable_min = cfg.get("dedup", {}).get("stable_min_runs", 3)

    src = os.path.join(_ROOT, "results", "findings", "normalized", "findings.jsonl")
    rows = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]

    # --- Tầng 1: trong từng lần chạy ----------------------------------------
    per_run = {}
    by_run = collections.defaultdict(list)
    for r in rows:
        by_run[(r["tool"], r["run"])].append(r)
    for key, items in by_run.items():
        per_run[key] = (len(items), len(dedup_tool(items, tol)))

    # --- Tầng 2: gộp toàn bộ các lần chạy của cùng tool ----------------------
    deduped = []
    for tool in sorted({r["tool"] for r in rows}):
        deduped.extend(dedup_tool([r for r in rows if r["tool"] == tool], tol))

    out_path = os.path.join(_ROOT, "results", "findings", "normalized", "deduped.jsonl")
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        for d in sorted(deduped, key=lambda d: (d["tool"], d["file"], d["line_min"] or 0)):
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")

    # --- Tầng 3: giao giữa hai tool -----------------------------------------
    matches, tools = cross_tool_overlap(deduped, tol)

    # --- Báo cáo -------------------------------------------------------------
    print(f"Dung sai dòng: ±{tol}   |   'ổn định' = xuất hiện ở >= {stable_min} lần chạy")
    print()
    print("TẦNG 1 — trùng lặp NGAY TRONG một lần chạy")
    print(f"{'TOOL':<15}{'RUN':<14}{'THÔ':>6}{'DUY NHẤT':>10}{'TỰ TRÙNG':>10}")
    print("-" * 56)
    for (tool, run), (raw, uniq) in sorted(per_run.items()):
        print(f"{tool:<15}{run:<14}{raw:>6}{uniq:>10}{raw - uniq:>10}")

    print()
    print("TẦNG 2 — gộp cả 3 lần chạy, theo độ lặp lại")
    print(f"{'TOOL':<15}{'DUY NHẤT':>10}{'3/3 RUN':>10}{'2/3':>7}{'1/3':>7}{'% ỔN ĐỊNH':>11}")
    print("-" * 62)
    stats = {}
    for tool in tools:
        ds = [d for d in deduped if d["tool"] == tool]
        c = collections.Counter(d["run_count"] for d in ds)
        stable = sum(v for k, v in c.items() if k >= stable_min)
        pct = stable / len(ds) * 100 if ds else 0
        print(f"{tool:<15}{len(ds):>10}{c.get(3,0):>10}{c.get(2,0):>7}{c.get(1,0):>7}{pct:>10.1f}%")
        stats[tool] = {
            "unique": len(ds), "stable": stable,
            "seen_3of3": c.get(3, 0), "seen_2of3": c.get(2, 0), "seen_1of3": c.get(1, 0),
            "in_test_code": sum(1 for d in ds if d["in_test_code"]),
            "no_cwe": sum(1 for d in ds if not d["cwe"]),
        }

    print()
    print(f"TẦNG 3 — cả hai tool cùng thấy: {len(matches)} vấn đề")
    if matches:
        print("  (khớp theo file + CWE; dòng chỉ ép khi cả hai bên đều đáng tin)")
        for m in matches[:8]:
            print(f"    {m['cwe']:<9} {m['file'].split('/')[-1]:<42}")
    else:
        print("  KHÔNG có giao nhau — xem phần diễn giải trong docs/stage6-dedup.md")

    out_counts = os.path.join(_ROOT, "results", "stats", "counts.json")
    with open(out_counts, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({
            "line_tolerance": tol, "stable_min_runs": stable_min,
            "per_tool": stats,
            "per_run": {f"{t}/{r}": {"raw": a, "unique": b} for (t, r), (a, b) in per_run.items()},
            "cross_tool_matches": len(matches),
            "cross_tool_detail": matches,
        }, fh, ensure_ascii=False, indent=2)
    print()
    print(f"-> {os.path.relpath(out_path, _ROOT)}")
    print(f"-> {os.path.relpath(out_counts, _ROOT)}")


if __name__ == "__main__":
    main()
