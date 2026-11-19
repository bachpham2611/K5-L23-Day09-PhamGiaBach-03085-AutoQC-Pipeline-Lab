"""BƯỚC 2/4 — Sửa tự động những lỗi bắt buộc mà máy sửa được AN TOÀN.   ★★

Test:  pytest tests/test_autofix.py -q

`fix(ds, issues, cfg) -> list[dict]`: duyệt `issues` (chỉ lỗi severity ERROR), sửa thẳng vào thư mục `ds`,
và trả về danh sách thay đổi; mỗi phần tử có khoá: image, line, action, before, after
(đây chính là file `03_fixes.csv` — bằng chứng pipeline đã làm gì, người có thể kiểm lại).

| code lỗi                                       | cách sửa                                    | action           |
|------------------------------------------------|---------------------------------------------|------------------|
| BAD_FORMAT, BAD_CLASS_ID, BOX_ZERO, DUP_BOX    | xoá dòng đó khỏi file nhãn                  | "drop_line"      |
| BOX_OUT_OF_IMAGE                               | cắt box về trong [0,1] (giữ nguyên lớp)     | "clip_box"       |
| MISSING_LABEL_FILE                             | KHÔNG sửa file (máy không thể bịa nhãn): ghi 1 dòng fix, before="(không có file)", after="chuyển người gán" | "needs_label" |
| BAD_CLASS_NAME                                 | ghi lại `names` trong data.yaml đúng `cfg["classes"]` | "rename_classes" |
| DUP_IMAGE_*, LEAK_TEST, CORRUPT_IMAGE, BAD_SIZE| xoá ảnh + nhãn khỏi `ds`                    | "remove_image"   |

Bẫy cần nghĩ tới (có test cho từng cái)
- Một file có NHIỀU dòng lỗi: số dòng trong `issues` là số dòng GỐC. Nếu xoá từng dòng một thì số dòng lệch.
  Gom theo file, rồi ghi lại MỘT lần.
- Sau khi cắt, box có thể không còn diện tích (nằm hẳn ngoài ảnh): lúc đó xoá dòng thay vì ghi box rỗng.
- Một dòng có 2 lỗi (vd. BOX_OUT_OF_IMAGE + DUP_BOX): xoá dòng thắng, chỉ ghi 1 fix `drop_line`, không ghi thêm `clip_box`. Dòng trống ở giữa file vẫn đếm vào số dòng gốc.
- Ảnh vừa bị xoá (DUP_IMAGE_*...) mà còn issue dòng của chính nó: bỏ qua êm, không được báo lỗi/chết.
- Không động tới lỗi WARN (cảnh báo là việc của người). Không sửa file ở `data/raw/` — chỉ sửa `ds`.
- Chạy lần 2 trên dữ liệu đã sửa phải không làm gì thêm (idempotent) — NGOẠI LỆ duy nhất: `needs_label` được ghi lại mỗi lần còn lỗi `MISSING_LABEL_FILE` (vì ảnh vẫn chưa có nhãn).

- `needs_label` KHÔNG tạo file nhãn rỗng (rỗng = "ảnh nền", khác với "chưa gán"). `review_queue` sẽ đẩy ảnh này lên đầu hàng đợi cho người.

Gợi ý: `yaml.safe_load / safe_dump`.
"""
from collections import defaultdict

import yaml

from ..common import ERROR, parse_yolo_line

DROP_CODES = {"BAD_FORMAT", "BAD_CLASS_ID", "BOX_ZERO", "DUP_BOX"}
REMOVE_CODES = {"LEAK_TEST", "CORRUPT_IMAGE", "BAD_SIZE"}


def _rec(image, line, action, before, after):
    return {"image": image, "line": line, "action": action, "before": before, "after": after}


def _is_remove(code):
    return code in REMOVE_CODES or code.startswith("DUP_IMAGE")


def fix(ds, issues, cfg):
    errors = [i for i in issues if i.severity == ERROR]
    fixes = []

    # 1) xoá ảnh (+ nhãn) trước; mỗi ảnh chỉ ghi 1 lần dù dính nhiều lỗi
    removed = set()
    for i in errors:
        if _is_remove(i.code) and i.image not in removed:
            removed.add(i.image)
            img = ds.image_path(i.image)
            before = img.name if img else i.image
            if img:
                img.unlink(missing_ok=True)
            ds.label_path(i.image).unlink(missing_ok=True)
            fixes.append(_rec(i.image, None, "remove_image", before, "(đã xoá ảnh và nhãn)"))

    # 2) gom lỗi theo file, theo số dòng GỐC
    drops, clips, missing, rename = defaultdict(set), defaultdict(set), [], False
    for i in errors:
        if i.image in removed:
            continue
        if i.code in DROP_CODES and i.line:
            drops[i.image].add(i.line)
        elif i.code == "BOX_OUT_OF_IMAGE" and i.line:
            clips[i.image].add(i.line)
        elif i.code == "MISSING_LABEL_FILE" and i.image not in missing:
            missing.append(i.image)
        elif i.code == "BAD_CLASS_NAME":
            rename = True

    # 3) mỗi file nhãn: đọc 1 lần, xử lý theo số dòng gốc, ghi lại 1 lần
    for stem in sorted(set(drops) | set(clips)):
        path = ds.label_path(stem)
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
        out, changed = [], False
        for no, text in enumerate(raw, 1):
            if no in drops[stem]:                       # xoá thắng cắt: dòng 2 lỗi chỉ ghi 1 fix
                fixes.append(_rec(stem, no, "drop_line", text, "(đã xoá dòng)"))
                changed = True
                continue
            if no in clips[stem]:
                try:
                    c, x, y, w, h = parse_yolo_line(text)
                except ValueError:
                    out.append(text)
                    continue
                x1, x2 = max(0.0, x - w / 2), min(1.0, x + w / 2)
                y1, y2 = max(0.0, y - h / 2), min(1.0, y + h / 2)
                if x2 - x1 <= 0 or y2 - y1 <= 0:        # nằm hẳn ngoài ảnh: không ghi box rỗng
                    fixes.append(_rec(stem, no, "drop_line", text, "(đã xoá dòng: box nằm ngoài ảnh)"))
                else:
                    new = f"{c} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} {y2 - y1:.6f}"
                    fixes.append(_rec(stem, no, "clip_box", text, new))
                    out.append(new)
                changed = True
                continue
            out.append(text)
        if changed:
            path.write_text("\n".join(out) + "\n" if out else "", encoding="utf-8")

    # 4) thiếu file nhãn: không bịa, chuyển cho người
    for stem in missing:
        fixes.append(_rec(stem, None, "needs_label", "(không có file)", "chuyển người gán"))

    # 5) data.yaml: ghi lại names đúng danh sách dự án
    if rename:
        before = dict(ds.yaml_names())
        d = {}
        if ds.yaml_path.exists():
            d = yaml.safe_load(ds.yaml_path.read_text(encoding="utf-8")) or {}
        else:
            d["path"] = "."
        d["names"] = {k: n for k, n in enumerate(cfg["classes"])}
        ds.yaml_path.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")
        fixes.append(_rec("data.yaml", None, "rename_classes", str(before), str(d["names"])))

    return fixes