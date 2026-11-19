"""CHECK 2 — Dòng nhãn YOLO: sai định dạng, ID lớp lạ, box tràn ảnh, box rỗng, box lặp.   (Script 2 trong slide)   ★★

Test:  pytest tests/test_label_format.py -q

Mỗi dòng nhãn: `id cx cy w h` (5 số, toạ độ 0–1). Trả về list[Issue]; `Issue.line` = số dòng (đếm từ 1).

| code             | severity | khi nào                                                                  |
|------------------|----------|--------------------------------------------------------------------------|
| BAD_FORMAT       | ERROR    | không đúng 5 cột số (dùng `parse_yolo_line`, bắt `ValueError`)           |
| BAD_CLASS_ID     | ERROR    | id lớp ngoài 0..len(ds.classes)-1                                        |
| BOX_ZERO         | ERROR    | w <= 0 hoặc h <= 0                                                       |
| BOX_OUT_OF_IMAGE | ERROR    | mép box vượt [0,1] quá `cfg["label_format"]["overflow_tol"]`; extra={"overflow": mức tràn} |
| DUP_BOX          | ERROR    | cùng lớp với một dòng TRƯỚC ĐÓ và IoU > `dup_box_iou`; báo ở dòng SAU    |
| BOX_TINY         | WARN     | cạnh ngắn của box (px) < `min_box_px`  (cần `ds.image_size(stem)`)       |
| BOX_HUGE         | WARN     | w*h > `max_box_frac`                                                     |

Gợi ý
- Mép trái = x - w/2, mép phải = x + w/2 (xem câu hỏi "dòng YOLO nào lỗi?" trong slide).
- Một dòng hỏng (BAD_FORMAT, BAD_CLASS_ID, BOX_ZERO) thì `continue`, đừng kiểm tiếp hình học của dòng đó.
- Dòng thiếu cột làm script của slide dừng giữa chừng: đây là lý do phải `try/except`.
- `iou_xywh(a, b)` có sẵn trong common.py.
- Ngưỡng nằm trong `cfg["label_format"]` — đừng viết cứng số vào code.
"""
from ..common import ERROR, WARN, Issue, iou_xywh, parse_yolo_line  # noqa: F401

CHECK = "label_format"


def run(ds, cfg):
    c = cfg["label_format"]
    n_classes = len(ds.classes)
    issues = []

    for path in ds.label_files():
        stem = path.stem
        size = ds.image_size(stem)          # (rộng, cao) px, hoặc None nếu không có/không mở được ảnh
        seen = []                           # các box hợp lệ ở dòng TRƯỚC: [(id lớp, (cx, cy, w, h))]

        # read_label_lines bỏ dòng trống nhưng GIỮ số dòng gốc nên báo lỗi đúng dòng
        for line_no, text in ds.read_label_lines(stem) or []:
            # 1) đúng 5 cột số?
            try:
                cls, x, y, w, h = parse_yolo_line(text)
            except ValueError as e:
                issues.append(Issue(CHECK, ERROR, "BAD_FORMAT", stem, f"sai định dạng: {e}", line=line_no))
                continue

            # 2) id lớp hợp lệ?
            if not 0 <= cls < n_classes:
                issues.append(Issue(CHECK, ERROR, "BAD_CLASS_ID", stem, f"id lớp {cls} ngoài 0..{n_classes - 1}", line=line_no))
                continue

            # 3) box có diện tích?
            if w <= 0 or h <= 0:
                issues.append(Issue(CHECK, ERROR, "BOX_ZERO", stem, f"box rỗng: w={w}, h={h}", line=line_no))
                continue

            # 4) tràn ảnh: lấy mép vượt xa nhất trong 4 mép
            overflow = max(-(x - w / 2), (x + w / 2) - 1, -(y - h / 2), (y + h / 2) - 1)
            if overflow > c["overflow_tol"]:
                issues.append(Issue(CHECK, ERROR, "BOX_OUT_OF_IMAGE", stem, f"box tràn ảnh {overflow:.4f}",
                                    line=line_no, extra={"overflow": overflow}))

            # 5) trùng với box cùng lớp ở dòng trước (báo ở dòng sau)
            box = (x, y, w, h)
            if any(pc == cls and iou_xywh(pb, box) > c["dup_box_iou"] for pc, pb in seen):
                issues.append(Issue(CHECK, ERROR, "DUP_BOX", stem, "box lặp với một dòng trước (cùng lớp)", line=line_no))
            seen.append((cls, box))

            # 6) cảnh báo: box quá nhỏ (đổi sang px) / quá lớn
            if size is not None:
                short_px = min(w * size[0], h * size[1])
                if short_px < c["min_box_px"]:
                    issues.append(Issue(CHECK, WARN, "BOX_TINY", stem, f"cạnh ngắn {short_px:.1f}px < {c['min_box_px']}px", line=line_no))
            if w * h > c["max_box_frac"]:
                issues.append(Issue(CHECK, WARN, "BOX_HUGE", stem, f"box chiếm {w * h:.0%} ảnh", line=line_no))

    return issues
