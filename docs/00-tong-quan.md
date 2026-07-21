# Giai đoạn 0 — Tổng quan & định nghĩa "thắng" (cho người mới)

Trước khi đụng vào script, hãy nắm mấy khái niệm nền. Nếu bạn đã rành SAST thì
lướt nhanh; nếu mới học thì đọc kỹ phần này, các doc sau sẽ dựa vào nó.

## 1. Vài từ khoá

- **SAST** (Static Application Security Testing): tìm lỗ hổng bảo mật bằng cách
  **đọc mã nguồn** mà *không chạy* chương trình. Ngược với DAST (chạy app rồi tấn công).
- **LLM-based SAST**: thay vì luật cứng (regex/AST rule), ta đưa code cho một
  **mô hình ngôn ngữ lớn (LLM)** đọc và phán "chỗ này có lỗ hổng gì". Bài benchmark
  này so các *harness* (khung điều phối) khác nhau **cùng dùng một LLM**.
- **Harness / skill**: phần "khung" bao quanh LLM — nó quyết định chia code ra sao,
  hỏi LLM câu gì, lọc kết quả thế nào. Chính cái khung này là thứ ta đem so.
- **Target**: mã nguồn đem đi quét. Ở đây là **WebGoat** — một app Java/Spring do
  OWASP cố tình nhồi lỗ hổng để dạy học, nên nó là "bãi tập" lý tưởng.
- **Finding**: một báo cáo "ở file X dòng Y có lỗ hổng loại Z".
- **CWE** (Common Weakness Enumeration): mã chuẩn phân loại lỗ hổng. Ví dụ
  `CWE-89` = SQL Injection, `CWE-79` = Cross-Site Scripting (XSS).

## 2. Ba con số phải báo cáo song song

Đừng bao giờ chỉ khoe một con số `#findings`. Luôn đi kèm bộ ba:

```
#findings   = số finding thô tool báo ra
#TP         = số True Positive  (finding ĐÚNG, có lỗ thật, sau khi judge)
precision   = TP / (TP + FP)     (tỉ lệ báo đúng)
```

- **True Positive (TP)**: tool báo có lỗ, và *thật sự có lỗ*. 👍
- **False Positive (FP)**: tool báo có lỗ, nhưng *không có lỗ*. 👎 (nhiễu)
- **False Negative (FN)**: có lỗ thật nhưng tool *bỏ sót*. (dùng cho recall)

Nếu có **ground truth** (đáp án) — WebGoat có sẵn theo từng "lesson" — thì tính thêm:

```
recall = TP / (TP + FN)          (bắt được bao nhiêu % lỗ thật có trong đề)
F1     = 2 * precision * recall / (precision + recall)   (điểm cân bằng)
```

## 3. Vì sao "nhiều nhất" là metric bẫy

Giả sử tool A báo 100 finding nhưng chỉ 10 đúng (precision 10%). Tool B báo 20
finding, 18 đúng (precision 90%). Nếu chỉ nhìn count thô, A "thắng". Nhưng người
dùng thật sẽ *ghét* A: phải lọc tay 90 báo động giả. **B mới là tool tốt.**

Vì thế phần "đo precision" (Giai đoạn 7) không phải phụ — nó là thứ **biến count
thô thành kết luận có nghĩa**.

## 4. Bẫy "apple-to-orange" (so táo với cam)

WebGoat là **Java/Spring**. Có tool trong bảng (Vulnhuntr) vốn *thiết kế cho Python*.
Đem nó quét Java thì gần như chắc chắn đuối — **không phải vì nó dở, mà vì nó chơi
sai sân**. Nếu để chung bảng xếp hạng mà không chú thích, kết luận sẽ sai lệch.

Cách xử lý (đã mã hoá trong `config/benchmark.yaml`):
- Nhóm `fair`: tool hỗ trợ Gemini + hợp Java -> **so trực tiếp**.
- Nhóm `claude_locked`: tool khoá cứng Claude, không chạy Gemini được -> **để riêng**,
  hoặc chấp nhận đổi model và ghi rõ đây là *confound* (biến gây nhiễu).
- Tool lệch ngôn ngữ (Vulnhuntr) -> tắt mặc định, chỉ bật để tham chiếu.

## 5. Bản đồ giai đoạn (đọc theo thứ tự)

1. **Cố định biến** ([stage1](stage1-co-dinh-bien.md)) — làm mọi thứ giống nhau
   trừ "con tool", nếu không so sánh vô nghĩa.
2. **Môi trường offline** ([stage2](stage2-moi-truong-offline.md)) — clone WebGoat
   đúng SHA, cắt web tool để tái lập được.
3. **LLM proxy** ([stage3](stage3-llm-proxy.md)) — đặt một trạm giữa để đo token &
   thời gian *khách quan*, không tin số tool tự khai.
4→9. Chạy, chuẩn hoá JSONL, dedup, judge precision, tối ưu, báo cáo (dựng sau).

> Nguyên tắc xuyên suốt: **định nghĩa trước khi chạy**. Chốt "thắng là gì", chốt
> biến nào cố định, chốt schema — rồi mới bấm nút. Nếu không, lúc đọc số sẽ cãi nhau.
