#!/usr/bin/env python3
"""
stage5_normalize.py — Giai đoạn 5: đưa output mọi tool về MỘT schema chung.

Vì sao cần: mỗi tool nói một thứ tiếng khác nhau, dù cùng mác "SARIF 2.1.0".
Đã đo được trên dữ liệu thật (2026-07-21):

  |                | SAIST                    | Metis                        |
  |----------------|--------------------------|------------------------------|
  | ruleId         | datadog/java-sqli (4-12) | AI001 (DUY NHẤT 1 rule)      |
  | dấu phân cách  | src/main/java/...  (/)   | src\\main\\java\\... (\\)       |
  | startLine      | dòng thật                | 56% là 1 (KHÔNG dùng được)   |
  | file test      | 0%                       | 8%                           |
  | CWE            | không có (suy từ slug)   | không có (suy từ tiêu đề)    |

Nếu gộp thẳng hai bên lại thì dedup hỏng 100% (chỉ riêng dấu phân cách đã làm
mọi cặp file không khớp), và mọi thống kê theo CWE đều rỗng.

Chạy:
    uv run --with pyyaml python scripts/stage5_normalize.py

Đầu ra:
    results/findings/normalized/findings.jsonl   — mỗi dòng 1 finding, schema chung
    results/stats/cost_by_run.json               — token/chi phí quy về từng run
"""
import hashlib
import json
import os
import re
import sys

import yaml

# Console Windows mặc định cp1252 -> in tiếng Việt là UnicodeEncodeError, và
# print() xuất CRLF làm bẩn dữ liệu khi bash đọc bằng $(...). Ép UTF-8 + LF ngay
# tại nguồn, giống scripts/bench_config.py.
sys.stdout.reconfigure(encoding="utf-8", newline="\n")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CFG = os.path.join(_ROOT, "config", "benchmark.yaml")


# ---------------------------------------------------------------------------
#  BẢNG SUY CWE
# ---------------------------------------------------------------------------
#  Không tool nào phát ra CWE, nên ta phải suy. Hai nguồn có độ tin cậy KHÁC NHAU
#  và schema phải ghi rõ điều đó — nếu không, người đọc báo cáo sẽ tưởng CWE của
#  Metis chắc chắn ngang CWE của SAIST.
#
#  SAIST: suy từ slug rule -> ánh xạ 1-1, tất định. Độ tin cậy CAO.
_SAIST_RULE_CWE = {
    "sqli": "CWE-89",
    "xss": "CWE-79",
    "cmdi": "CWE-78",
    "xpathi": "CWE-643",
    "pathtraversal": "CWE-22",
    "ssrf": "CWE-918",
    "ldapi": "CWE-90",
    "deserialization": "CWE-502",
    "xxe": "CWE-611",
    "crypto": "CWE-327",
    "secrets": "CWE-798",
}

#  Metis: chỉ có tiêu đề tự do -> khớp từ khoá, HEURISTIC. Độ tin cậy THẤP.
#  Thứ tự quan trọng: mẫu cụ thể phải đứng trước mẫu chung (vd "sql injection"
#  trước "injection"), nếu không sẽ khớp nhầm.
_METIS_TITLE_CWE = [
    (r"sql\s*injection", "CWE-89"),
    (r"cross[- ]site\s*scripting|xss", "CWE-79"),
    (r"command\s*injection|os\s*command", "CWE-78"),
    (r"deserializ", "CWE-502"),
    (r"path\s*traversal|directory\s*traversal", "CWE-22"),
    (r"hardcoded\s*(credential|password|secret|key)", "CWE-798"),
    (r"xml\s*external|xxe", "CWE-611"),
    (r"ssrf|server[- ]side\s*request", "CWE-918"),
    (r"null\s*pointer", "CWE-476"),
    (r"race\s*condition", "CWE-362"),
    (r"sensitive\s*(data|information)\s*expos", "CWE-200"),
    (r"weak\s*(crypto|cipher|hash)|insecure\s*random", "CWE-327"),
    (r"ldap\s*injection", "CWE-90"),
    (r"open\s*redirect", "CWE-601"),
    (r"csrf|cross[- ]site\s*request", "CWE-352"),
    (r"xpath\s*injection", "CWE-643"),
    (r"log\s*injection|log\s*forg", "CWE-117"),
    (r"insecure\s*direct\s*object|idor", "CWE-639"),
]

# Đường dẫn được coi là mã KIỂM THỬ, không phải mã sản phẩm.
_TEST_PATH = re.compile(r"(^|/)(test|tests|it)/", re.IGNORECASE)

# SARIF level -> mức nghiêm trọng chung.
_LEVEL_SEVERITY = {"error": "high", "warning": "medium", "note": "low", "none": "info"}


def load_cfg():
    with open(_CFG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def norm_path(uri: str) -> str:
    """
    Chuẩn hoá đường dẫn file.

    Metis xuất `src\\main\\java\\...` (backslash Windows), SAIST xuất
    `src/main/java/...`. Không thống nhất thì dedup GIỮA hai tool hỏng 100% —
    mọi cặp file đều "khác nhau" dù trỏ cùng một file.
    """
    if not uri:
        return ""
    p = uri.replace("\\", "/")
    p = re.sub(r"^file:/*", "", p)
    # Bỏ tiền tố tuyệt đối nếu tool ghi cả đường dẫn máy.
    m = re.search(r"(src/(main|test|it)/.*)$", p)
    if m:
        p = m.group(1)
    # CHỈ bỏ tiền tố "./", KHÔNG dùng lstrip("./") — lstrip xoá cả TẬP ký tự
    # {'.', '/'}, nên ".mvn/wrapper/X.java" bị biến thành "mvn/wrapper/X.java"
    # và file không còn tồn tại. Đã xảy ra thật: judge ở Giai đoạn 7 không đọc
    # được file và bỏ qua finding.
    return re.sub(r"^(\./)+", "", p)


def cwe_from_text(text: str):
    """Suy CWE từ nội dung mô tả. Dùng chung cho cả hai tool."""
    t = (text or "").lower()
    for pattern, cwe in _METIS_TITLE_CWE:
        if re.search(pattern, t):
            return cwe
    return None


def cwe_for_saist(rule_id: str, message: str):
    """
    Suy CWE cho SAIST bằng CÁCH ĐỐI CHIẾU CHÉO hai nguồn: slug rule và nội dung
    message do LLM viết.

    Ban đầu tôi chỉ dùng slug và gán confidence "high" vì ánh xạ 1-1, tất định.
    ĐO TRÊN DỮ LIỆU THẬT thì sai: **37% finding có ruleId mâu thuẫn với chính
    message của nó** — 24 finding gắn rule `java-xss` nhưng message mô tả SQL
    injection, 10 finding `java-xss` nhưng mô tả path traversal.

    Lý do: SAIST chạy prompt của TỪNG rule lên file; LLM đôi khi báo một lỗ hổng
    KHÁC với thứ rule đang tìm, nhưng finding vẫn giữ ID của rule đó. Tin slug là
    tin cái nhãn, không phải tin cái nội dung.

    Quy ước:
      hai nguồn khớp        -> "high"
      chỉ có slug           -> "medium"   (message không nói rõ loại lỗ hổng)
      hai nguồn MÂU THUẪN   -> "conflict" và LẤY THEO MESSAGE, vì message là lập
                               luận thực tế của model, còn slug chỉ là nhãn rule.
    """
    slug = (rule_id or "").rsplit("/", 1)[-1]        # datadog/java-sqli -> java-sqli
    slug = re.sub(r"^(java|go|py|js|ts|rb|php|cs)-", "", slug)
    by_rule = _SAIST_RULE_CWE.get(slug)
    by_msg = cwe_from_text(message)

    if by_rule and by_msg:
        if by_rule == by_msg:
            return (by_rule, "high")
        return (by_msg, "conflict")
    if by_rule:
        return (by_rule, "medium")
    if by_msg:
        return (by_msg, "low")
    return (None, "none")


def cwe_for_metis(title: str):
    """Suy CWE từ tiêu đề tự do của Metis. Heuristic -> confidence thấp."""
    t = (title or "").lower()
    for pattern, cwe in _METIS_TITLE_CWE:
        if re.search(pattern, t):
            return (cwe, "low")
    return (None, "none")


def line_info(region: dict, tool: str):
    """
    Lấy số dòng + ĐÁNH GIÁ nó có dùng được không.

    Metis đặt startLine=1 cho 56% finding trong khi snippet lại trích dòng 28-32
    -> số dòng đó VÔ NGHĨA. Nếu im lặng dùng nó, dedup theo (file, dòng) sẽ gộp
    nhầm hàng loạt finding khác nhau trong cùng file thành một.
    Ta GIỮ số dòng nhưng gắn cờ, để Giai đoạn 6 chọn khoá dedup cho đúng.
    """
    start = (region or {}).get("startLine")
    if start is None:
        return None, "missing"
    if tool == "arm-metis" and start == 1:
        return start, "unreliable"
    return start, "exact"


def normalize_result(res: dict, tool: str, run: str, phase: str) -> dict:
    loc = (res.get("locations") or [{}])[0].get("physicalLocation", {}) or {}
    raw_uri = (loc.get("artifactLocation") or {}).get("uri", "")
    path = norm_path(raw_uri)
    start, line_conf = line_info(loc.get("region"), tool)

    msg = (res.get("message") or {}).get("text", "") or ""
    rule_id = res.get("ruleId") or ""

    if tool == "datadog-saist":
        cwe, cwe_conf = cwe_for_saist(rule_id, msg)
        title = rule_id
    else:
        cwe, cwe_conf = cwe_for_metis(msg)
        # Metis chỉ có 1 ruleId (AI001) nên ruleId vô dụng để phân loại;
        # tiêu đề trong message MỚI là thứ mang thông tin.
        title = msg.split("\n")[0][:200]

    level = (res.get("level") or "").lower()

    # ID ổn định để đối chiếu giữa các lần chạy. KHÔNG đưa `run` vào hash —
    # mục đích là nhận ra "cùng một finding" xuất hiện ở nhiều run.
    ident = f"{tool}|{rule_id}|{path}|{start}|{title}"
    finding_id = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:16]

    return {
        "finding_id": finding_id,
        "tool": tool,
        "run": run,
        "phase": phase,
        "file": path,
        "start_line": start,
        "line_confidence": line_conf,
        "rule_id_raw": rule_id,
        "title": title,
        "cwe": cwe,
        "cwe_confidence": cwe_conf,      # high = suy từ slug; low = đoán từ chữ
        "severity": _LEVEL_SEVERITY.get(level, "unknown"),
        "level_raw": level,
        "in_test_code": bool(_TEST_PATH.search("/" + path)),
        "message": msg[:2000],
    }


def cost_for_run(calls, meta):
    """
    Quy token/chi phí về đúng lần chạy, dùng lát cắt dòng mà orchestrator ghi.
    Cắt theo DÒNG chứ không theo thời gian: hai lần chạy nối đuôi nhau thì mốc
    thời gian chồng lấn, còn số dòng thì không bao giờ nhập nhằng.
    """
    lo = meta.get("call_log_line_from")
    hi = meta.get("call_log_line_to")
    if not lo or not hi or hi < lo:
        return None
    sl = calls[lo - 1:hi]                      # 1-indexed -> 0-indexed
    return {
        "llm_calls": len(sl),
        "input_tokens": sum(c.get("prompt_tokens") or 0 for c in sl),
        "output_tokens": sum(c.get("completion_tokens") or 0 for c in sl),
        "cost_usd": round(sum(c.get("cost_usd") or 0 for c in sl), 6),
        "via_fallback_calls": sum(1 for c in sl if c.get("via_fallback")),
        "unknown_tool_calls": sum(1 for c in sl if c.get("tool") == "unknown"),
    }


def main():
    cfg = load_cfg()
    call_log = os.path.join(_ROOT, cfg["proxy"]["call_log"])
    calls = []
    if os.path.exists(call_log):
        with open(call_log, encoding="utf-8") as fh:
            calls = [json.loads(l) for l in fh if l.strip()]

    findings_dir = os.path.join(_ROOT, "results", "findings")
    out_dir = os.path.join(findings_dir, "normalized")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(_ROOT, "results", "stats"), exist_ok=True)

    rows, cost_rows, skipped = [], [], []

    for tool in sorted(os.listdir(findings_dir)):
        tool_dir = os.path.join(findings_dir, tool)
        if not os.path.isdir(tool_dir) or tool in ("normalized", "_invalid"):
            continue
        for run in sorted(os.listdir(tool_dir)):
            run_dir = os.path.join(tool_dir, run)
            meta_p = os.path.join(run_dir, "run_meta.json")
            sarif_p = os.path.join(run_dir, "raw_output.sarif")
            if not os.path.exists(meta_p):
                continue
            meta = json.load(open(meta_p, encoding="utf-8"))

            # CHỈ nhận run hợp lệ. Run dính rate limit vẫn có SARIF "đọc được"
            # nhưng nội dung rỗng — trộn vào là làm hỏng thống kê.
            if meta.get("valid") is False:
                skipped.append(f"{tool}/{run} (valid=false)")
                continue

            c = cost_for_run(calls, meta)
            cost_rows.append({
                "tool": tool, "run": run, "phase": meta.get("phase"),
                "wall_clock_s": meta.get("wall_clock_s"),
                "target_sha": meta.get("target_sha"),
                **(c or {}),
            })

            if not os.path.exists(sarif_p):
                continue
            try:
                sarif = json.load(open(sarif_p, encoding="utf-8"))
            except Exception as e:
                skipped.append(f"{tool}/{run} (SARIF hỏng: {e})")
                continue
            for sr in sarif.get("runs", []):
                for res in sr.get("results", []):
                    rows.append(normalize_result(res, tool, run, meta.get("phase")))

    out_findings = os.path.join(out_dir, "findings.jsonl")
    with open(out_findings, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    out_cost = os.path.join(_ROOT, "results", "stats", "cost_by_run.json")
    with open(out_cost, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cost_rows, fh, ensure_ascii=False, indent=2)

    # --- Tóm tắt ra màn hình -------------------------------------------------
    import collections
    print(f"Đã chuẩn hoá {len(rows)} finding -> {os.path.relpath(out_findings, _ROOT)}")
    print(f"Chi phí {len(cost_rows)} lần chạy -> {os.path.relpath(out_cost, _ROOT)}")
    if skipped:
        print("BỎ QUA:", ", ".join(skipped))
    print()
    by_tool = collections.Counter(r["tool"] for r in rows)
    print(f"{'TOOL':<15} {'FINDING':<9} {'CÓ CWE':<8} {'CWE XUNG ĐỘT':<14} {'DÒNG XẤU':<10} {'FILE TEST'}")
    print("-" * 74)
    for tool, n in sorted(by_tool.items()):
        rs = [r for r in rows if r["tool"] == tool]
        cwe = sum(1 for r in rs if r["cwe"])
        conflict = sum(1 for r in rs if r["cwe_confidence"] == "conflict")
        bad = sum(1 for r in rs if r["line_confidence"] != "exact")
        tst = sum(1 for r in rs if r["in_test_code"])
        print(f"{tool:<15} {n:<9} {cwe:<8} {conflict:<14} {bad:<10} {tst}")
    print()
    conf = collections.Counter((r["tool"], r["cwe_confidence"]) for r in rows)
    print("Phân bố cwe_confidence (high=2 nguồn khớp, conflict=rule vs message mâu thuẫn):")
    for (tool, c), n in sorted(conf.items()):
        print(f"  {tool:<15} {c:<10} {n}")


if __name__ == "__main__":
    main()
