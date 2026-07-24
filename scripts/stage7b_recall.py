#!/usr/bin/env python3
"""
stage7b_recall.py — đo recall Ở MỨC LESSON.

Precision (Giai đoạn 7) trả lời "tool báo có đúng không". Recall trả lời câu còn
lại: "tool BỎ SÓT bao nhiêu". Thiếu recall thì một tool im lặng an toàn (precision
100% vì chỉ báo 1 finding chắc ăn) trông ngang một tool tìm được mọi thứ.

CẢNH BÁO PHƯƠNG PHÁP — đọc trước khi tin số:
  WebGoat không có ground truth máy đọc ở mức dòng. Ta xấp xỉ bằng cấu trúc
  lessons/<tên>/: mỗi lesson dạy một lớp lỗ hổng (khai trong benchmark.yaml).
  "Recall" ở đây = "tool có tìm được lỗ hổng ĐÚNG LỚP (đúng CWE) bên trong lesson
  dạy lớp đó không". Đây là recall THÔ ở mức lesson, KHÔNG phải recall ở mức dòng.
  Nó có thể ĐÁNH GIÁ CAO HƠN thực tế: tool báo đúng CWE ở nhầm dòng vẫn được tính.

Chạy:
    uv run --with pyyaml python scripts/stage7b_recall.py
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
_LESSON = re.compile(r"lessons/([^/]+)/")


def main():
    with open(_CFG, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    gt = cfg["ground_truth"]["lessons"]
    # Chỉ giữ lesson CÓ CWE kỳ vọng. Lesson null (intro/tutorial) ra khỏi mẫu số.
    expected = {L: cwe for L, cwe in gt.items() if cwe}

    J = [json.loads(l) for l in
         open(os.path.join(_ROOT, "results", "findings", "normalized", "judged.jsonl"),
              encoding="utf-8") if l.strip()]

    # Với mỗi (tool, lesson): tool có TP nào mang đúng CWE của lesson đó không?
    # Dùng judge_cwe (CWE mà judge xác nhận) khi có, fallback về cwe đã suy.
    hit = collections.defaultdict(set)          # tool -> {lesson bắt đúng CWE}
    hit_any = collections.defaultdict(set)      # tool -> {lesson có TP bất kỳ}
    tools = sorted({r["tool"] for r in J})

    for r in J:
        if r["verdict"] != "TP":
            continue
        m = _LESSON.search(r["file"])
        if not m or m.group(1) not in expected:
            continue
        lesson = m.group(1)
        hit_any[r["tool"]].add(lesson)
        want = expected[lesson]
        got = r.get("judge_cwe") or r.get("cwe")
        if got and got.upper().replace("CWE-", "CWE-") == want:
            hit[r["tool"]].add(lesson)

    n = len(expected)
    print(f"Ground truth: {n} lesson có CWE kỳ vọng "
          f"(bỏ {len(gt) - n} lesson không phải lỗ hổng)")
    print(f"Recall = số lesson tool bắt đúng CWE / {n}\n")

    print(f"{'TOOL':<15}{'ĐÚNG CWE':>10}{'RECALL':>9}{'CÓ TP BẤT KỲ':>14}{'RECALL NỚI':>12}")
    print("-" * 60)
    rows = {}
    for t in tools:
        strict = len(hit[t]); loose = len(hit_any[t])
        print(f"{t:<15}{strict:>10}{strict/n*100:>8.1f}%{loose:>14}{loose/n*100:>11.1f}%")
        rows[t] = {"recall_strict": strict, "recall_loose": loose, "denominator": n,
                   "lessons_hit_cwe": sorted(hit[t]),
                   "lessons_hit_any": sorted(hit_any[t])}

    # Lesson KHÔNG tool nào bắt đúng CWE -> điểm mù chung, đáng đưa vào báo cáo.
    all_hit = set().union(*hit.values()) if hit else set()
    blind = sorted(set(expected) - all_hit)
    print(f"\nĐiểm mù (không tool nào bắt đúng CWE): {len(blind)}/{n}")
    if blind:
        print("  " + ", ".join(f"{L}({expected[L]})" for L in blind))

    out = os.path.join(_ROOT, "results", "stats", "recall.json")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"granularity": "lesson", "denominator": n,
                   "per_tool": rows, "blind_spots": blind,
                   "caveat": "recall ở mức lesson, xấp xỉ thô, có thể cao hơn recall mức dòng"},
                  fh, ensure_ascii=False, indent=2)
    print(f"\n-> {os.path.relpath(out, _ROOT)}")


if __name__ == "__main__":
    main()
