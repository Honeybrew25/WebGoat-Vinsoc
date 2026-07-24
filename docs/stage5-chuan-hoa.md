# Giai đoạn 5 — Chuẩn hoá về một schema chung

> Mục tiêu: biến output "mỗi tool một kiểu" thành **một file JSONL duy nhất** mà
> Giai đoạn 6 (dedup) và 7 (judge) dùng được, và quy token/chi phí về từng lần chạy.

```bash
uv run --with pyyaml python scripts/stage5_normalize.py
```

Đầu ra:
- `results/findings/normalized/findings.jsonl` — mỗi dòng một finding
- `results/stats/cost_by_run.json` — token/chi phí/thời gian từng lần chạy

## 1. Vì sao "cùng SARIF 2.1.0" vẫn không gộp được

Cả hai tool đều xuất SARIF 2.1.0. Nghe như xong việc. Thực tế đo được:

| | SAIST | Metis |
|---|---|---|
| `ruleId` | `datadog/java-sqli` (4–12 rule) | `AI001` — **duy nhất 1 rule** |
| Dấu phân cách | `src/main/java/…` | `src\main\java\…` |
| `startLine` | dòng thật | **56% bằng 1** (vô nghĩa) |
| Finding trong file test | 0% | 8% |
| CWE | không có | không có |

Gộp thẳng thì hỏng theo ba cách, hai trong đó **im lặng**:

**Dấu phân cách** — `src\main\A.java` và `src/main/A.java` là hai chuỗi khác nhau,
nên dedup giữa hai tool sẽ ra 0% trùng lặp. Không có lỗi nào báo; chỉ là kết quả sai.

**Số dòng của Metis** — 56% finding ghi `startLine: 1` trong khi `snippet` lại trích
dòng 28–32. Dùng khoá `(file, dòng)` sẽ **gộp nhầm hàng loạt** finding khác nhau
trong cùng file thành một. Ta **giữ số dòng nhưng gắn cờ** `line_confidence`, để
Giai đoạn 6 tự chọn khoá phù hợp thay vì tin mù.

**CWE** — không tool nào phát ra. Phải suy, và hai nguồn suy **không cùng độ tin cậy**.

## 2. Schema chung

```json
{
  "finding_id": "a1b2c3d4e5f6a7b8",
  "tool": "arm-metis", "run": "run-01-cold", "phase": "cold",
  "file": "src/main/java/org/owasp/webgoat/…/Servers.java",
  "start_line": 57,
  "line_confidence": "exact | unreliable | missing",
  "rule_id_raw": "AI001",
  "title": "SQL Injection",
  "cwe": "CWE-89",
  "cwe_confidence": "high | medium | conflict | low | none",
  "severity": "high | medium | low | info",
  "in_test_code": false,
  "message": "…"
}
```

### `cwe_confidence` — và một phát hiện làm tôi phải viết lại phần này

Ban đầu tôi định nghĩa đơn giản: SAIST suy CWE từ slug rule (`java-sqli` → CWE-89),
ánh xạ 1-1 tất định nên gán `high`; Metis chỉ có tiêu đề tự do nên gán `low`.

**Đo trên dữ liệu thật thì giả định đó sai.** Đối chiếu `ruleId` với chính `message`
mà LLM viết ra:

```
SAIST finding kiểm tra được: 104
ruleId MÂU THUẪN với nội dung message: 38 (37%)
   24x  rule=datadog/java-xss    -> CWE-79   nhưng message mô tả SQL injection
   10x  rule=datadog/java-xss    -> CWE-79   nhưng message mô tả path traversal
    4x  rule=datadog/java-xpathi -> CWE-643  nhưng message mô tả XSS
```

Nguyên nhân: SAIST chạy prompt của **từng rule** lên file. LLM đôi khi báo một lỗ
hổng **khác** với thứ rule đang tìm, nhưng finding vẫn **giữ ID của rule đó**. Tin
slug là tin cái *nhãn*, không phải tin cái *nội dung*.

Bộ chuẩn hoá vì thế **đối chiếu chéo hai nguồn**:

| Tình huống | `cwe_confidence` | CWE lấy theo |
|---|---|---|
| slug và message khớp nhau | `high` | cả hai |
| chỉ slug có tín hiệu | `medium` | slug |
| **slug và message mâu thuẫn** | **`conflict`** | **message** |
| chỉ message có tín hiệu (Metis) | `low` | message |
| không nguồn nào | `none` | — |

Khi mâu thuẫn ta lấy theo **message**, vì đó là lập luận thực tế của model; slug chỉ
là nhãn của rule đã được kích hoạt.

Phân bố thực tế:

| Tool | high | medium | **conflict** | low | none |
|---|---|---|---|---|---|
| datadog-saist | 66 | 16 | **41 (33%)** | 0 | 0 |
| arm-metis | 0 | 0 | 0 | 540 | 294 (35%) |

Hai tool **không đáng tin theo hai kiểu khác nhau**: Metis không có taxonomy nào cả
(100% CWE là phỏng đoán từ chữ, 35% không suy nổi); SAIST *có* taxonomy nhưng nhãn
của nó tự mâu thuẫn ở 1/3 số finding. Giai đoạn 7 phải judge dựa trên **message**,
không dựa trên `ruleId`.

### `in_test_code` — gắn cờ, KHÔNG xoá

8% finding của Metis nằm trong `src/test/` và `src/it/` (SAIST: 0%). Lỗ hổng trong
mã kiểm thử thường không phải rủi ro thật. Nhưng quyết định "có tính hay không" là
của Giai đoạn 7, không phải của bộ chuẩn hoá — nên ở đây chỉ **gắn cờ**. Bộ chuẩn
hoá xoá dữ liệu là bộ chuẩn hoá đang lén ra quyết định thay bạn.

## 3. Chỉ nhận run `valid: true`

Run dính rate limit vẫn sinh SARIF **đọc được** nhưng nội dung gần rỗng (đã xảy ra:
1 call LLM, 0 finding, `exit_code: 0`). Trộn vào là kéo tụt thống kê của tool đó mà
không ai thấy. Bộ chuẩn hoá đọc `valid` trong `run_meta.json` và bỏ qua run hỏng,
có in ra danh sách đã bỏ.

## 4. Quy chi phí về từng run — cắt theo DÒNG, không theo thời gian

`cost_for_run()` dùng `call_log_line_from/to` mà orchestrator ghi lại. Cắt theo mốc
thời gian sẽ nhập nhằng khi hai lần chạy nối đuôi nhau hoặc có call về muộn; số dòng
thì không bao giờ nhập nhằng.

## 5. Kết quả trên dữ liệu thật (2026-07-21)

957 finding từ 6 lần chạy hợp lệ.

| Tool | Finding | Có CWE | CWE xung đột | Dòng không tin được | File test |
|---|---|---|---|---|---|
| arm-metis | 834 | 540 | 0 | **469 (56%)** | 69 |
| datadog-saist | 123 | 123 | **41 (33%)** | 0 | 0 |

### Chi phí quy về từng run

| Tool | Giây | Call | Token vào | Token ra | USD |
|---|---|---|---|---|---|
| arm-metis run-01-**cold** | 142 | 934 | 4,450,890 | 134,931 | **1.3151** |
| arm-metis run-02-warm | 140 | 934 | 4,450,890 | 135,354 | **0.8331** |
| arm-metis run-03-warm | 181 | 934 | 4,450,890 | 134,867 | **0.8416** |
| datadog-saist run-01-cold | 16 | 162 | 668,599 | 13,170 | 0.1869 |
| datadog-saist run-02-warm | 11 | 174 | 660,833 | 14,986 | 0.1827 |
| datadog-saist run-03-warm | 13 | 171 | 677,267 | 14,955 | 0.1759 |

**Cold/warm hiện ra ở CHI PHÍ, không chỉ ở thời gian.** Metis gửi **đúng 4,450,890
token vào cả ba lần** (chunking tất định), nhưng lần cold tốn **$1.32** còn warm chỉ
**$0.83** — rẻ hơn 37% nhờ prompt caching. Nếu gộp chung 3 run rồi chia trung bình,
con số này biến mất và ta báo cáo sai chi phí vận hành thực tế.

**Tổng:** Metis $2.99 / 463s / 2,802 call — SAIST $0.55 / 40s / 507 call.
Metis đắt gấp **5.5×** và chậm gấp **11.6×**, đổi lấy nhiều finding hơn ~6.7×.
Đắt/rẻ mỗi TP thật thì phải chờ Giai đoạn 7.

## 6. ĐÍNH CHÍNH số liệu độ ổn định ở Giai đoạn 4

Ở [stage4](stage4-chay-va-do.md) tôi báo Metis ổn định **98.9%** còn SAIST **52.7%**.
Con số đó dùng khoá `(ruleId, file, startLine)` — **không công bằng**, vì Metis chỉ
có một `ruleId` duy nhất và 56% `startLine` bằng 1, nên khoá của nó thực chất co lại
gần bằng "tên file", thô hơn hẳn khoá của SAIST.

Tính lại bằng khoá công bằng cho cả hai — `(title, file)`, bỏ số dòng vì Metis không
dùng được:

| Tool | Khoá cũ (rule, file, **dòng**) | Khoá công bằng (title, file) |
|---|---|---|
| arm-metis | 98.9% | **97.1%** |
| datadog-saist | 52.7% | **73.2%** |

Kết luận định tính **không đổi** — Metis ổn định hơn SAIST rõ rệt — nhưng khoảng
cách bị tôi phóng đại: 46 điểm thành **24 điểm**.

Và lộ ra một điều mới: SAIST nhảy từ 52.7% lên 73.2% khi bỏ số dòng, nghĩa là phần
lớn "bất ổn định" của nó là **trôi số dòng** — báo đúng vấn đề ở đúng file nhưng lệch
dòng giữa các lần chạy. Đó là dạng bất ổn nhẹ hơn nhiều so với việc tìm ra vấn đề
khác hẳn. Giai đoạn 6 nên dedup với dung sai vài dòng thay vì khớp chính xác.

## 7. Xong Giai đoạn 5 khi...

- [x] `findings.jsonl` sinh ra được, 957 finding từ 6 run hợp lệ.
- [x] Path đã chuẩn hoá về `/`; `line_confidence` và `cwe_confidence` có giá trị đúng.
- [x] `cost_by_run.json` khớp: tổng call theo run = số dòng `calls.jsonl` của run đó,
      `via_fallback` và `unknown` đều 0.
- [ ] Giai đoạn 6 dùng file này để dedup.

➡️ Tiếp theo (Giai đoạn 6): dedup trong-run và giữa-run, rồi đếm. Lưu ý sẵn: Metis
báo 278 kết quả nhưng chỉ ~262 duy nhất — có trùng lặp ngay trong một lần chạy.
