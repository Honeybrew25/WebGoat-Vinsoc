# Giai đoạn 10 — CI regression gate

> ✅ **KẾT LUẬN:** Mỗi pull request chạy một strict gate offline; bất kỳ test,
> artifact, provenance, metric, Pareto, budget, cú pháp hoặc report freshness
> nào sai đều block PR.

## 1. Chạy local

```bash
# Chạy đủ sáu check
uv run --with pyyaml python scripts/stage10_ci_gate.py

# Liệt kê check hoặc chạy một nhóm để chẩn đoán
uv run --with pyyaml python scripts/stage10_ci_gate.py --list
uv run --with pyyaml python scripts/stage10_ci_gate.py \
  --only stage9-report,yaml

# Kiểm whitespace trên khoảng thay đổi so với một commit gốc
uv run --with pyyaml python scripts/stage10_ci_gate.py \
  --base-ref <git-revision>
```

## 2. Sáu check bắt buộc

| Check | Kiểm tra | Khi thất bại |
|---|---|---|
| `unit-tests` | Toàn bộ `unittest` trong `tests/` | Có regression hành vi hoặc contract |
| `python-compile` | Compile `scripts/` và `proxy/token_logger.py` | Python có lỗi cú pháp/bytecode |
| `yaml` | Parse benchmark, proxy và workflow YAML | Cấu hình hoặc workflow không còn hợp lệ |
| `bash-syntax` | `bash -n` cho `scripts/*.sh`, `adapters/*.sh` | Script shell có lỗi cú pháp |
| `stage9-report` | `stage9_report.py --check` | Artifact, provenance, metric, Pareto, budget hoặc report bị stale/mâu thuẫn |
| `whitespace` | `git diff --check` trên working tree hoặc PR range | Diff có whitespace error |

## 3. Chính sách lỗi

- Gate luôn chạy hết các check đã chọn để trả về đầy đủ lỗi trong một lần.
- Exit `0` chỉ khi tất cả check đạt; exit `1` khi có check lỗi; exit `2` khi
  tham số CLI không hợp lệ.
- Timeout, executable bị thiếu và exception đều là lỗi blocking.
- Gate không tự sửa report stale. Việc tái sinh bằng `--write` phải là quyết
  định có chủ đích sau khi xác nhận source artifact thay đổi hợp lệ.
- Trong GitHub Actions, cùng kết quả được nối vào `GITHUB_STEP_SUMMARY`.

## 4. Offline và read-only

Gate không gọi scanner, API hoặc judge; không khởi động proxy; không chạy
`--write`; không sửa artifact Stage 4–9. Workflow không nhận secret và chỉ có
quyền `contents: read`.

## 5. GitHub Actions

Workflow `.github/workflows/benchmark-regression.yml` chạy khi:

- mở hoặc cập nhật pull request;
- push vào `main`;
- chạy thủ công bằng `workflow_dispatch`.

Job dùng Ubuntu, Python 3.12, timeout 10 phút và concurrency
`cancel-in-progress`. `actions/checkout` và `astral-sh/setup-uv` được pin bằng
commit SHA; uv được pin phiên bản. Pull request truyền base SHA qua
`STAGE10_BASE_REF` để check đúng toàn bộ PR diff.

## 6. Chẩn đoán

- Report stale:

  ```bash
  uv run --with pyyaml python scripts/stage9_report.py --check
  ```

  Chỉ chạy `stage9_report.py --write` khi thay đổi source artifact là có chủ
  đích và đã được review.

- Lỗi một nhóm:

  ```bash
  uv run --with pyyaml python scripts/stage10_ci_gate.py --only <check-id>
  ```

- Thiếu Bash: Cài Bash và đảm bảo `bash` có trong `PATH`.

- Whitespace:

  ```bash
  git diff --check HEAD --
  ```

- Base revision:

  ```bash
  git rev-parse --verify --end-of-options <revision>^{commit}
  ```

Không bỏ check hoặc đổi strict gate thành warning để làm xanh CI.
