#!/usr/bin/env python3
"""
stage7c_judge_agreement.py — kiểm định độ tuần hoàn của judge.

Lỗ hổng phương pháp: Giai đoạn 7 dùng judge CÙNG model (flash-lite) với tool được
chấm. Model có xu hướng tán thành lập luận do chính lớp model đó sinh ra -> có thể
thổi phồng precision một cách hệ thống.

Cách kiểm: chấm lại đúng tập đó bằng một judge ĐỘC LẬP, khác model, rồi đo mức
đồng thuận. Tên file/model được truyền bằng CLI để phương pháp không gắn cứng
vào một endpoint cụ thể.

  - Đồng thuận cao  -> phán quyết vững, không phụ thuộc chọn judge. Yên tâm dùng số.
  - Đồng thuận thấp -> precision ở Giai đoạn 7 là tạo tác của việc chọn judge,
                       phải báo cáo khoảng chứ không phải một số.

Điều quan trọng cần soi: chỗ hai judge KHÔNG đồng thuận có LỆCH VỀ MỘT TOOL không?
Nếu judge yếu dễ dãi ĐÚNG với tool cùng model của nó, đó là bằng chứng tuần hoàn
trực tiếp.

Chạy:
    uv run --with pyyaml python scripts/stage7c_judge_agreement.py
"""
import argparse
import collections
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", newline="\n")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NORM = os.path.join(_ROOT, "results", "findings", "normalized")


def load(name):
    p = os.path.join(_NORM, name)
    return {r["finding_id"] if "finding_id" in r else
            (r["tool"], r["file"], r.get("line_min"), r["title"]): r
            for r in (json.loads(l) for l in open(p, encoding="utf-8") if l.strip())}


def key(r):
    return (r["tool"], r["file"], r.get("line_min"), r["title"])


def agreement_metrics(weak_verdicts, strong_verdicts):
    if not weak_verdicts or len(weak_verdicts) != len(strong_verdicts):
        raise ValueError("hai dãy verdict phải khác rỗng và có cùng độ dài")
    n = len(weak_verdicts)
    agree = sum(w == s for w, s in zip(weak_verdicts, strong_verdicts))
    observed = agree / n
    weak_tp = sum(v == "TP" for v in weak_verdicts) / n
    strong_tp = sum(v == "TP" for v in strong_verdicts) / n
    expected = weak_tp * strong_tp + (1 - weak_tp) * (1 - strong_tp)
    kappa = (observed - expected) / (1 - expected) if expected < 1 else 1.0
    return {"n": n, "agreement": observed, "cohens_kappa": kappa}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weak-file", default="judged.jsonl")
    ap.add_argument("--strong-file", default="judged-independent.jsonl")
    ap.add_argument("--strong-label", default="gemini-3-flash independent")
    args = ap.parse_args()

    weak = {key(r): r for r in
            (json.loads(l) for l in open(os.path.join(_NORM, args.weak_file),
                                         encoding="utf-8") if l.strip())}
    strong = {key(r): r for r in
              (json.loads(l) for l in open(os.path.join(_NORM, args.strong_file),
                                           encoding="utf-8") if l.strip())}

    both = sorted(set(weak) & set(strong))
    print(f"Judge yếu (flash-lite): {len(weak)} finding")
    print(f"Judge độc lập ({args.strong_label}): {len(strong)} finding")
    print(f"Chấm được bởi cả hai  : {len(both)}\n")

    metrics = agreement_metrics(
        [weak[k]["verdict"] for k in both],
        [strong[k]["verdict"] for k in both],
    )
    n = metrics["n"]
    po = metrics["agreement"]
    kappa = metrics["cohens_kappa"]
    agree = round(po * n)
    print(f"ĐỒNG THUẬN: {agree}/{n} = {po*100:.1f}%")
    print(f"Cohen's kappa: {kappa:.3f}  "
          f"({'gần như hoàn hảo' if kappa>0.8 else 'đáng kể' if kappa>0.6 else 'vừa phải' if kappa>0.4 else 'yếu'})")

    # Bất đồng lệch về tool nào? Đây là phép thử tuần hoàn.
    print("\nBẤT ĐỒNG (judge yếu nói TP, judge mạnh nói FP = judge yếu có thể đã dễ dãi):")
    lenient = collections.Counter()
    strict = collections.Counter()
    for k in both:
        w, s = weak[k]["verdict"], strong[k]["verdict"]
        if w == "TP" and s == "FP":
            lenient[weak[k]["tool"]] += 1
        elif w == "FP" and s == "TP":
            strict[weak[k]["tool"]] += 1
    for tool in sorted({t for t, *_ in both}):
        print(f"  {tool:<15} yếu dễ dãi hơn: {lenient[tool]:>3}   |   yếu khắt khe hơn: {strict[tool]:>3}")

    # Precision theo từng judge, đặt cạnh nhau.
    print("\nPRECISION theo từng judge:")
    print(f"  {'TOOL':<15}{'FLASH-LITE':>12}{'ĐỘC LẬP':>10}{'CHÊNH':>9}")
    precision_by_tool = {}
    for tool in sorted({t for t, *_ in both}):
        ks = [k for k in both if k[0] == tool]
        wp = sum(1 for k in ks if weak[k]["verdict"] == "TP") / len(ks)
        sp = sum(1 for k in ks if strong[k]["verdict"] == "TP") / len(ks)
        precision_by_tool[tool] = {
            "weak": round(wp, 4), "independent": round(sp, 4),
        }
        print(f"  {tool:<15}{wp*100:>11.1f}%{sp*100:>9.1f}%{(sp-wp)*100:>+8.1f}")

    out = os.path.join(_ROOT, "results", "stats", "judge_agreement.json")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({
            "n": n, "agreement": round(po, 4), "cohens_kappa": round(kappa, 4),
            "weak_file": args.weak_file, "independent_file": args.strong_file,
            "independent_label": args.strong_label,
            "weak_lenient_by_tool": dict(lenient),
            "weak_strict_by_tool": dict(strict),
            "precision_by_tool": precision_by_tool,
        }, fh, ensure_ascii=False, indent=2)
    print(f"\n-> {os.path.relpath(out, _ROOT)}")


if __name__ == "__main__":
    main()
