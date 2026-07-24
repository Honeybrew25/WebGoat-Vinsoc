# Giai đoạn 1 — Cố định biến để công bằng

## Ý tưởng một câu

> Muốn biết **"con tool nào giỏi"**, thì mọi thứ **trừ con tool** phải giống hệt
> nhau. Nếu tool A dùng model xịn hơn, hoặc quét phiên bản code khác, thì A thắng
> cũng chẳng chứng minh được gì.

Đây là tư duy **thí nghiệm có đối chứng** (controlled experiment): giữ nguyên mọi
"biến", chỉ đổi *một* biến bạn muốn đo (ở đây: harness/skill SAST).

## Các biến phải đóng băng

Tất cả nằm trong `config/benchmark.yaml`. Đọc kèm file đó cho dễ hình dung.

### 1. Cùng mã nguồn target — pin SHA

```yaml
target:
  sha: c3ed45a733377bc7313b93f57ff518254d81380f   # WebGoat v2025.3
```

**"Pin SHA" là gì?** Mỗi lần ai đó sửa code trên GitHub, Git tạo một "commit" có
mã băm (SHA) riêng. Repo luôn thay đổi theo thời gian. Nếu tool A quét hôm nay,
tool B quét tuần sau, chúng có thể đọc *code khác nhau* -> so sánh vô nghĩa.

Bằng cách khoá về đúng 1 SHA, ta đảm bảo **mọi tool đọc chính xác cùng những dòng
code**. Giai đoạn 2 sẽ `git checkout` đúng SHA này.

### 2. Cùng model — `gemini-3.1-flash-lite`

```yaml
model:
  id: gemini-3.1-flash-lite
  temperature: 0      # càng thấp càng ổn định, dễ tái lập
  top_p: 1
```

- **Cùng model** để không ai được lợi thế "não to hơn".
- **Model thật và alias là hai thứ khác nhau.** `model.id` ở trên là model thật
  của Google. Các tên `gemini-31-flash-lite-saist`, `...-metis`, `...-judge` chỉ
  là alias nội bộ trên LiteLLM để quy token về đúng bên gọi; tất cả cùng trỏ tới
  `gemini/gemini-3.1-flash-lite` với cùng tham số.
- **Lịch sử thử model:** key Gemini cũ bị giới hạn free-tier **15 RPM**, nên các
  mẻ thử trên model khác đã không hợp lệ. Sau khi có key Gemini mới, toàn bộ 6 run
  chính thức được chạy lại trên `gemini-3.1-flash-lite`, 0 rate-limit và 0
  fallback (chi tiết: [Stage 4](stage4-chay-va-do.md)). Không trộn số liệu từ các
  mẻ thử model trước vào bảng chính.
- **Đánh đổi của model nhẹ:** model nhẹ thì *mọi* tool đều moi được ít TP
  hơn, khoảng cách giữa các tool bị nén lại. Nếu bảng xếp hạng cuối quá sát nhau,
  đó là dấu hiệu cần chạy lại trên model mạnh hơn — **không phải** bằng chứng các
  tool ngang tài.
- API báo 0 thinking token trong prompt SAST đã đo; nếu nhà cung cấp có trả
  reasoning token thì logger vẫn tính chúng vào `completion_tokens` và chi phí.
- **temperature = 0**: LLM có yếu tố ngẫu nhiên; temperature cao -> trả lời khác
  nhau mỗi lần. Đặt 0 để kết quả *ổn định nhất có thể* (tái lập được). Lưu ý:
  không phải tool nào cũng cho bạn set — xem cột `controllable` trong bảng tool.
  Đã kiểm chứng `gemini-3.1-flash-lite` **chấp nhận** `temperature=0` (một số reasoning
  model từ chối). Phải thử lại bằng call thật mỗi lần đổi model: `drop_params:
  true` trong `litellm_config.yaml` sẽ **âm thầm bỏ** tham số bị API từ chối.

### 3. Cùng ngân sách context (nếu tool cho phép)

```yaml
model:
  max_input_tokens: 1000000  # dưới context API 1,048,576 token
  max_output_tokens: 8192
```

Nếu tool A được nhồi cả repo vào 1 prompt còn tool B chỉ được xem 1 file, thì
khác biệt đến từ *ngân sách*, không phải *chất lượng harness*. Đặt trần chung khi
có thể.

## Ma trận tool — ai được "so kèo cùng model"?

Không phải tool nào cũng cho trỏ sang model tuỳ chọn. Đây là chỗ **dễ làm hỏng
benchmark nhất**. Ta chia nhóm (cột `group` trong config; cột `byo_model` = tool
có cho "mang model của bạn tới" qua endpoint OpenAI-compatible không):

| Tool | BYO model? | Nhóm | Xử lý |
|---|---|---|---|
| **SAIST (Datadog)** | ✅ (cần cờ `--ai-gateway`) | `fair` | So trực tiếp. Có sẵn detect→validate (lọc FP) |
| **Arm Metis** | ✅ (model-agnostic) | `fair` | So trực tiếp. Có tree-sitter call graph đa file |
| **Vulnhuntr** | ✅ nhưng | (tắt) | *Python-focused* -> lệch sân Java, tắt mặc định |
| **Claude Code Security Review** | ❌ (khoá Claude) | `claude_locked` | Để nhóm riêng, KHÔNG so chung |
| **Anthropic Defending Code** | ❌ (khoá Claude) | `claude_locked` | Để nhóm riêng |

### Quy tắc vàng
- **Chỉ nhóm `fair` mới lên chung một bảng xếp hạng "cùng model".**
- Nhóm `claude_locked`: nếu muốn đưa vào, phải chấp nhận **đổi model** và ghi rõ
  đây là **confound** (biến gây nhiễu) — kết luận yếu hơn hẳn.
- Vulnhuntr: chơi sai sân. Hoặc loại, hoặc để riêng và **note rõ**, đừng để nó
  kéo tụt kết luận chung.

## "Controllable" nghĩa là gì?

Một số tool cho bạn chỉ định model + temperature qua config/flag (`controllable: yes`).
Một số chỉ cho chọn model, không cho chỉnh temperature (`partial`). Khi tool
*không* cho set temperature, ta ghi chú lại — đó là một khác biệt không kiểm soát
được, cần minh bạch trong báo cáo cuối.

## Xong Giai đoạn 1 khi...

- [x] `config/benchmark.yaml` đã pin `target.sha`, `model.id`, `model.temperature`.
- [x] Bảng tool đã phân nhóm `fair` / `claude_locked` / tắt.
- [x] Đã chốt `internet.mode` (mặc định `webtools_off`).

➡️ Tiếp theo: [Giai đoạn 2 — dựng môi trường offline](stage2-moi-truong-offline.md),
biến cái SHA đã pin thành mã nguồn thật trên máy.
