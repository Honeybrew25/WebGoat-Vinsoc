# Giai đoạn 7 — Precision bằng LLM-as-judge

```bash
uv run --with pyyaml python scripts/stage7_judge.py --limit 5   # thử
uv run --with pyyaml python scripts/stage7_judge.py             # chấm hết
```

Đầu ra: `results/findings/normalized/judged.jsonl`, `results/stats/precision.json`

Đây là giai đoạn biến `#findings` — con số đếm thô, gian lận được bằng cách spam —
thành `#TP` và `precision`, thứ duy nhất làm bảng xếp hạng có nghĩa.

## 1. KẾT QUẢ

Phạm vi chung: **Java, không phải mã kiểm thử, không phải thư viện đóng gói sẵn**.

| Tool | Chấm | TP | FP | **Precision** | TP (3/3 run) | Precision ổn định |
|---|---|---|---|---|---|---|
| **arm-metis** | 192 | **159** | 33 | 82.8% | 157 | 82.6% |
| **datadog-saist** | 46 | 45 | **1** | **97.8%** | 30 | **100.0%** |

| Tool | Chi phí / TP | Thời gian / TP |
|---|---|---|
| arm-metis | $0.0188 | 2.9s |
| datadog-saist | $0.0121 | 0.9s |

### Đọc kết quả này thế nào

**Không có người thắng tuyệt đối** — và đó chính là điều
[stage0](00-tong-quan.md) cảnh báo từ đầu.

- Hỏi *"tool nào moi ra nhiều lỗ hổng THẬT nhất?"* → **Metis**, 159 TP so với 45,
  gấp **3.5×**. Nó thật sự tìm được nhiều thứ có thật hơn hẳn, không phải chỉ ồn.
- Hỏi *"tool nào đáng tin khi nó lên tiếng?"* → **SAIST**, precision **97.8%** so
  với 82.8%. Trên tập ổn định (3/3 lần chạy), SAIST đạt **100%** — mọi finding lặp
  lại được của nó đều là thật.
- Hỏi *"tool nào rẻ?"* → **SAIST**, rẻ hơn ~1.6× mỗi TP và nhanh hơn **3.2×**.

Đổi lại, dùng Metis nghĩa là phải lọc tay **33 báo động giả**; dùng SAIST là **1**.
Nhưng chọn SAIST là chấp nhận **bỏ sót 114 lỗ hổng thật** mà Metis tìm ra.

Với đội có người review, Metis đáng giá. Với gate CI tự động chặn merge, precision
của SAIST quan trọng hơn.

## 2. Bốn nguyên tắc thiết kế, mỗi cái chống một kiểu sai

**Chấm mù.** Judge không biết finding đến từ tool nào. Biết tên tool là mở đường cho
thiên vị mà ta không có cách nào kiểm chứng.

**Đọc `message`, không đọc `rule_id_raw`.** [Stage 5](stage5-chuan-hoa.md) đo được
33% finding của SAIST có `ruleId` mâu thuẫn với chính message của nó. Chấm theo nhãn
rule là chấm sai đối tượng. *Kiểm chứng ngược:* FP **duy nhất** của SAIST chính là
ca này — judge viết *"The tool misidentified a cryptographic weakness (CWE-327) as
an XSS vulnerability"*.

**Đưa cả file khi file đủ nhỏ** (≤400 dòng; trung vị WebGoat là 60). Đây là chỗ dễ
vô tình thiên vị nhất: 56% số dòng của Metis là rác (`startLine=1`). Nếu chỉ đưa cửa
sổ quanh dòng đó, judge đọc nhầm chỗ và chấm oan Metis. Với finding có dòng không
đáng tin, prompt còn **nói thẳng** cho judge biết đừng tin con số đó.

**Phạm vi chung.** Metis báo 65 finding trong `.js` (43 trong thư viện đóng gói:
jquery, bootstrap.min.js, underscore) — SAIST không quét `.js` lần nào. So thẳng là
so **phạm vi**, không phải so năng lực. Benchmark khai `target.language: java`, nên
bảng chính chỉ tính Java.

| Tool | Tổng | Java | JS | Thư viện | Test | **Trong phạm vi** |
|---|---|---|---|---|---|---|
| datadog-saist | 46 | 46 | 0 | 0 | 0 | **46** |
| arm-metis | 280 | 215 | 65 | 43 | 23 | **192** |

Lọc phạm vi làm khoảng cách co từ 6.1× xuống **4.2×** — tức là ~1/3 ưu thế số lượng
của Metis đến từ việc nó quét thêm ngôn ngữ khác, không phải từ việc đọc Java giỏi hơn.

## 3. Judge có đáng tin không?

**Tự nhất quán: 100%.** Chấm lại 50 finding (25 FP + 25 TP, mẫu cân bằng) ở
`temperature=0`: **50/50 giữ nguyên phán quyết, 0 đổi ý**. Precision đo được không
phải nhiễu ngẫu nhiên.

### Ba giới hạn phải ghi vào báo cáo

**Judge dùng CÙNG model với tool được chấm** (`gemini-3.1-flash-lite`). Đây là rủi
ro *tuần hoàn*: model có xu hướng tán thành lập luận do chính model đó sinh ra.
Cách khắc phục là chấm lại bằng model độc lập và khai báo rõ model thứ hai. Gemini
3 Flash đã chấm đủ 238 finding qua Batch API: đồng thuận 87.8%, Cohen's kappa
0.580. Precision giảm từ 82.8% xuống 77.1% cho Metis và từ 97.8% xuống 89.1% cho
SAIST. Thứ hạng không đổi nhưng số tuyệt đối nhạy với judge; xem [Stage
7c](stage7c-judge-doc-lap.md).

**Prompt có thể nghiêng về TP.** Nó dặn judge chấm TP *"kể cả khi file này là mã dạy
học cố tình có lỗ hổng"* — cần thiết vì WebGoat đúng là như vậy, nhưng cũng có thể
làm judge dễ dãi. Tỉ lệ TP tổng thể là **204/238 = 86%**, cao; hợp lý với một app cố
tình nhồi lỗ hổng, nhưng không loại trừ được khả năng judge dễ tính.

**Trường `confidence` vô dụng.** Judge trả `high` cho **100%** phán quyết — không
bao giờ tỏ ra do dự. Đừng dùng trường này để lọc; nó không mang thông tin.

## 4. FP trông như thế nào

FP của Metis phần lớn là **vấn đề chất lượng code bị gọi tên thành lỗ hổng bảo mật**,
hoặc lỗ hổng ở nơi không tiếp cận được:

- *"Insecure URL handling (SSRF)"* → judge: script tiện ích lúc build, không phơi ra web.
- *"Path Traversal in AsciiDoc template loading"* → judge: tên template phân giải theo
  classpath, không phải hệ thống file.
- *"Open Redirect"* → judge: đường dẫn dựng từ trạng thái nội bộ, không từ input người dùng.

Điều này khớp với dấu hiệu đã thấy ở [stage6](stage6-dedup.md): 99 finding của Metis
không suy nổi CWE vì tiêu đề mơ hồ kiểu *"Potential NullPointerException leading to
Denial of Service"* — đó là bug chất lượng, không phải lỗ hổng bảo mật.

## 5. Chi phí

Judge tốn **$0.10** cho 244 call (329,580 token vào / 13,444 ra). Alias riêng
`gemini-31-flash-lite-judge` giữ cho khoản này **không lẫn** vào chi phí của tool —
nếu chung alias, bảng "chi phí mỗi tool" sẽ bị cộng thêm tiền chấm điểm và mất nghĩa.

## 6. Bẫy đã gặp: `lstrip("./")` xoá cả tập ký tự

Judge ban đầu bỏ qua 2 finding vì "không đọc được file". Nguyên nhân nằm ở
[stage5](stage5-chuan-hoa.md), không phải ở judge: `norm_path()` kết thúc bằng
`p.lstrip("./")`. `lstrip` xoá **tập ký tự** `{'.', '/'}` ở đầu chuỗi, không phải
tiền tố `"./"` — nên `.mvn/wrapper/X.java` thành `mvn/wrapper/X.java`, một đường dẫn
không tồn tại. Đã đổi sang `re.sub(r"^(\./)+", "", p)`.

Loại bug này im lặng: nó chỉ ảnh hưởng vài file có tên bắt đầu bằng dấu chấm, và
biểu hiện ở một giai đoạn hoàn toàn khác nơi gây ra.

## 7. Xong Giai đoạn 7 khi...

- [x] 238 finding trong phạm vi chung đã chấm, 0 lỗi.
- [x] Chấm mù, đọc `message` không đọc `ruleId`, đưa cả file cho file nhỏ.
- [x] Judge tự nhất quán 100% trên mẫu cân bằng 50 finding.
- [x] Chi phí judge tách riêng qua alias, không lẫn vào chi phí tool.
- [x] Đã chấm đủ 238 finding bằng model độc lập và báo precision/agreement/kappa
  ([Stage 7c](stage7c-judge-doc-lap.md)).
- [x] Đã đo *recall* xấp xỉ theo lesson WebGoat; xem [Stage 7b](stage7b-recall.md).

➡️ Tiếp theo: Giai đoạn 8 (tối ưu) và 9 (báo cáo). Báo cáo cuối phải đặt hai bộ
precision cạnh nhau thay vì dùng một judge như ground truth tuyệt đối.
