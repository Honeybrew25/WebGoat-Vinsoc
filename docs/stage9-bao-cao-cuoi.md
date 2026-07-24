# Giai đoạn 9 — Báo cáo cuối

> ✅ **KẾT LUẬN:** Không có người thắng tuyệt đối theo cả hai tình huống; `arm-metis` phù hợp review chuyên sâu; `datadog-saist` phù hợp CI trên mỗi PR.

> **Review chuyên sâu:** dùng `arm-metis` để ưu tiên độ bao phủ, sau đó review thủ công các finding.

> **CI gate:** dùng `datadog-saist` trên mỗi PR; chạy `arm-metis` nightly hoặc trước release.

## 1. Tóm tắt điều hành

Benchmark cố định `gemini-3.1-flash-lite` trên WebGoat `c3ed45a73337` và dùng profile `balanced-v1`.

| Tool | TP / FP | Precision độc lập | Recall lesson | Stable TP | F1 xấp xỉ |
|---|---:|---:|---:|---:|---:|
| arm-metis | 148 / 44 | 77.08% | 13/22 | 146 | 0.669 |
| datadog-saist | 41 / 6 | 87.23% | 5/22 | 30 | 0.361 |

| Tool | Wall clock baseline → tối ưu | Δ | Token baseline → tối ưu | Δ | Cost baseline → tối ưu | Δ |
|---|---:|---:|---:|---:|---:|---:|
| arm-metis | 142s → 58s | -59.2% | 4,585,821 → 1,349,793 | -70.6% | $0.841598 → $0.445274 | -47.1% |
| datadog-saist | 13s → 11s | -15.4% | 681,769 → 670,560 | -1.6% | $0.182733 → $0.176114 | -3.6% |

Tổng chi Stage 8: **$1.872177/$5.000000**.

## 2. Benchmark identity và fairness

- Target: WebGoat `v2025.3` tại `c3ed45a733377bc7313b93f57ff518254d81380f`.
- Model: `gemini-3.1-flash-lite`, temperature `0`, top_p `1`.
- Phạm vi: `java`, loại test và vendor theo `config/benchmark.yaml`.
- Mỗi tool chạy 3 lần; báo cáo resource dùng median.

## 3. Chất lượng cuối và F1 xấp xỉ

Precision dùng judge độc lập. Recall và F1 chỉ là xấp xỉ mức lesson, không phải mức dòng.

- `arm-metis`: precision 77.08%, recall 13/22, F1 xấp xỉ 0.669.
- `datadog-saist`: precision 87.23%, recall 5/22, F1 xấp xỉ 0.361.

## 4. Độ nhạy theo judge

Trên Stage 7 baseline aligned verdict set, agreement là 87.8% và Cohen's kappa là 0.580. Đây là mức phụ thuộc judge đáng kể; không coi một precision là ground truth tuyệt đối.

| Tool | Precision cùng model | Precision độc lập | Chênh |
|---|---:|---:|---:|
| arm-metis | 82.8% | 77.1% | -5.7% |
| datadog-saist | 97.8% | 89.1% | -8.7% |

## 5. Điểm mù chung cuối

- `hijacksession` — `CWE-384`
- `htmltampering` — `CWE-602`
- `insecurelogin` — `CWE-319`
- `logging` — `CWE-117`
- `securepasswords` — `CWE-521`
- `spoofcookie` — `CWE-565`
- `ssrf` — `CWE-918`
- `vulnerablecomponents` — `CWE-1104`

## 6. Khuyến nghị triển khai

- Mỗi PR: `datadog-saist`.
- Nightly hoặc trước release: `arm-metis`.
- Hai công cụ phục vụ hai lịch khác nhau; `datadog-saist` cho CI và `arm-metis` cho coverage review.
- Các finding cần review thủ công; không dùng count thô để block merge.
- Báo cáo không tạo weighted score.

## 7. Giới hạn phương pháp

- Recall và F1 là xấp xỉ ở mức lesson, không phải mức dòng.
- Cohen's kappa được đo trên baseline Stage 7, không phải finding mới Stage 8.
- WebGoat cố tình chứa lỗ hổng và không đại diện cho mọi codebase production.
- Precision phụ thuộc lựa chọn judge; báo cáo giữ cả hai bộ baseline.

## 8. Tái lập báo cáo

```bash
uv run --with pyyaml python scripts/stage9_report.py --write
uv run --with pyyaml python scripts/stage9_report.py --check
```

## 9. Provenance

| Artifact | SHA-256 |
|---|---|
| `config/benchmark.yaml` | `ff15b45e9c609c697abce103e1463e945a23a3e5ed0800f6e20cf3e572265c0c` |
| `results/findings/normalized/judged-independent.jsonl` | `02db608c429fb55e317f3349cb0268dc02d3f6c3cc747ee3e856410dac076ffa` |
| `results/findings/normalized/judged.jsonl` | `80fb9f67e91ef9789f99b78b901f3b8bc847bd3a9f4a18115a78935819dfa63a` |
| `results/optimization/balanced-v1/profile.json` | `7600985530c8c57bef80cbade8d6f2d88e4c4336d74b00dac995b6544f7f2900` |
| `results/optimization/balanced-v1/stats/cost_by_run.json` | `77889031f50a49c6da7d9bb1c7433850bed450967ea15b51a04de12f103ca9b1` |
| `results/optimization/balanced-v1/stats/final.json` | `f11ff7519fc71ebd278b94f858a4d41b4169a9777acca13bd443e107682887b1` |
| `results/optimization/balanced-v1/stats/judge-state.json` | `bdfd70b46b04e7bb46c0a8a6b1442592606ad5589b80ba3e651a965186195f34` |
| `results/optimization/balanced-v1/stats/judged-final.jsonl` | `afe4a34d85b58d778c379e68da71ecfc911254280fd7e86ef0e752e717e35c58` |
| `results/optimization/balanced-v1/stats/judged-novel.jsonl` | `336f91780c42a79d11237b8d47c99197b55550b7f2818e54c0e3c9e97b8cca02` |
| `results/stats/cost_by_run.json` | `d64375c14460ac37d7f8e14cc457863bde276d556276c16cd7e1d8b408c00311` |
| `results/stats/counts.json` | `3dedf305ecf028a437fd8eefae84f1ca2ccce0bb036445cfea652d7e59cd997a` |
| `results/stats/judge_agreement.json` | `4cf4bc60a6f90efd97bfb2f96779623c1613242a16bf8c67078380a4be14262c` |
| `results/stats/precision-independent.json` | `c8acdcb13137c391d459d1577dee4eee439b330c8952e0232723e63d00388dde` |
| `results/stats/precision.json` | `725ec2d562f6d71bf530ab0494f04e2a5f7c9ad518c729cdc5bf5a12bb33803b` |
| `results/stats/recall.json` | `cfba485cf9797438f6bd50f2473f511212018f2bbc1b608c7ce03814dabd712b` |
