# Giai đoạn 2 — Dựng môi trường offline

## Mục tiêu

Biến cái SHA đã pin ở Giai đoạn 1 thành **mã nguồn thật trên máy**, và đảm bảo
mọi tool đọc **đúng bản đó**, **không lôi thêm gì từ internet**.

Hai lý do phải offline:
1. **Công bằng & tái lập**: nếu harness được search web, kết quả phụ thuộc "hôm
   nay Google index gì" — chạy lại ngày mai ra khác. Cắt web -> loại biến đó.
2. **Đúng phạm vi**: ta đang đo khả năng *đọc code tìm lỗ*, không phải khả năng
   *tra cứu internet*.

## Chạy

```bash
bash scripts/stage2_setup_target.sh          # clone + pin SHA + index
bash scripts/stage2_setup_target.sh --build  # thêm bước build warm maven cache
bash scripts/stage2_setup_target.sh --force  # xoá clone cũ làm lại từ đầu
```

## Script làm gì (từng bước)

| Bước | Việc | Vì sao |
|---|---|---|
| 1 | `git clone` WebGoat vào `target/WebGoat` | lấy mã nguồn về local |
| 2 | `git checkout <SHA>` | đóng băng đúng phiên bản v2025.3 |
| 3 | So `rev-parse HEAD` với SHA config, **fail nếu lệch** | chống "tưởng đúng mà sai bản" |
| 4 | Liệt kê file `.java` + đếm LoC -> `results/stats/` | index offline cho harness |
| 5 | *(tuỳ chọn)* `./mvnw compile` | warm cache Maven để lần sau chạy offline |

### Kết quả mẫu (lần chạy thực tế)

```
[✓] SHA khớp: c3ed45a733377bc7313b93f57ff518254d81380f
[✓] Index xong: 371 file Java, ~24508 dòng.
```

Sinh ra 2 file:
- `results/stats/target_java_files.txt` — danh sách 371 file Java (đường dẫn tương
  đối trong WebGoat). Adapter của mỗi tool sẽ nạp danh sách này để biết cần quét gì.
- `results/stats/target_index.json` — metadata: SHA, số file, tổng LoC, thời điểm index.

> **LoC để làm gì?** Biết quy mô target giúp (a) ước lượng chi phí token, (b) so
> "mật độ finding" giữa các tool cho công bằng.

## "Không dùng internet" — chốt nghĩa cho rõ

Đây là chỗ **bạn cần quyết**, đã để thành công tắc trong `config/benchmark.yaml`:

```yaml
internet:
  mode: webtools_off   # hoặc: airgapped
```

| mode | Web search/fetch trong harness | Gọi model hosted | Kiến trúc |
|---|---|---|---|
| `webtools_off` *(mặc định)* | ❌ CẤM | ✅ cho phép | Như hiện tại |
| `airgapped` | ❌ CẤM | ❌ CẤM | Phải đổi **model local** |

- **`webtools_off`**: scanner chỉ được đọc code trong `target/WebGoat`. Nhưng việc
  call tới model hosted (Google Gemini) là *bất khả kháng* nếu muốn dùng model đó —
  ta chấp nhận. Đây là lựa chọn mặc định, thực dụng.
- **`airgapped`**: cấm *mọi* outbound kể cả model. Khi đó model hosted **không
  dùng được**, phải chuyển sang model chạy local (Ollama, vLLM...) — thay đổi lớn.
  Nếu bạn cần cái này, báo mình để bàn lại phần model.

### Cách "cắt web tool" trên thực tế
Tuỳ harness, nhưng nguyên tắc chung khi cấu hình adapter (Giai đoạn 4):
- Tắt cờ web-search / web-fetch của tool nếu có.
- Không cấp API key cho công cụ tra cứu bên ngoài.
- Nếu tool cứng đầu, chặn ở tầng mạng (firewall/route) — chỉ chừa cổng proxy LLM.

## Xong Giai đoạn 2 khi...

- [x] `target/WebGoat` tồn tại, `git rev-parse HEAD` = SHA trong config.
- [x] `results/stats/target_index.json` có `num_java_files` > 0.
- [x] Đã chốt `internet.mode`.

➡️ Tiếp theo: [Giai đoạn 3 — LLM proxy đo token/time](stage3-llm-proxy.md).
Đây là "chiêu chính": đặt một trạm giữa để **đo token & thời gian khách quan**,
không phải tin con số tool tự khai.
