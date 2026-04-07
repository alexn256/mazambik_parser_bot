import re
import cv2
import numpy as np

from config import QUEUE_LABELS

# Regex for time ranges: handles "10:00 - 11:30", "10.00 — 11.30", etc.
TIME_RANGE_RE = re.compile(
    r"(\d{1,2})[:\.](\d{2})\s*[-\u2013\u2014~]+\s*(\d{1,2})[:\.](\d{2})"
)

# Regex for watermark timestamp: "10:43 3.4.2026"
WATERMARK_RE = re.compile(
    r"(\d{1,2})[:\.](\d{2})\s+(\d{1,2})\.(\d{1,2})\.(\d{4})"
)


def parse_schedule_image(image_path: str) -> dict:
    """Parse a schedule image and return structured data.

    Returns:
        {
            "timestamp": "10:43" or None,
            "date": "03.04.2026" or None,
            "schedule": {
                "1.1": [{"start": "10:00", "end": "11:30"}, ...],
                "1.2": [...],
                ...
            }
        }
    """
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Cannot read image: {image_path}")

    watermark_time, watermark_date = _extract_watermark(img)
    boxes = _extract_boxes(img)

    schedule = {}
    for label, box_img in zip(QUEUE_LABELS, boxes):
        time_ranges = _ocr_box(box_img)
        schedule[label] = time_ranges

    return {
        "timestamp": watermark_time,
        "date": watermark_date,
        "schedule": schedule,
    }


def _extract_watermark(img: np.ndarray) -> tuple[str | None, str | None]:
    """Extract timestamp and date from the watermark in the middle of the image."""
    import pytesseract

    h, w = img.shape[:2]
    # Watermark text sits at the very top of the middle band
    mid_region = img[int(h * 0.35):int(h * 0.40), int(w * 0.2):int(w * 0.8)]

    gray = cv2.cvtColor(mid_region, cv2.COLOR_BGR2GRAY)
    for thresh_val in [160, 170, 180, 190, 150, 140]:
        _, binary = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY_INV)
        scaled = cv2.resize(binary, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        text = pytesseract.image_to_string(scaled, lang="ukr", config="--psm 7")
        match = WATERMARK_RE.search(text)
        if match:
            hh, mm = match.group(1), match.group(2)
            day, month, year = match.group(3), match.group(4), match.group(5)
            timestamp = f"{int(hh):02d}:{mm}"
            date_str = f"{int(day):02d}.{int(month):02d}.{year}"
            return timestamp, date_str

    return None, None


def _extract_boxes(img: np.ndarray) -> list[np.ndarray]:
    """Extract 12 queue boxes from the bottom section of the image.

    Uses contour detection on saturated colored regions.
    Falls back to fixed grid positions if contour detection fails.
    """
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # Create mask of saturated colored regions in bottom half
    bottom_start = int(h * 0.58)
    bottom_hsv = hsv[bottom_start:, :]

    mask = (bottom_hsv[:, :, 1] > 80).astype(np.uint8) * 255

    # Morphological operations to clean up
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Filter contours by area — each box should be roughly 1/12 of the bottom area
    bottom_h = h - bottom_start
    expected_area = (bottom_h * w) / 12
    min_area = expected_area * 0.3
    max_area = expected_area * 2.0

    boxes_info = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if min_area < area < max_area:
            x, y, bw, bh = cv2.boundingRect(cnt)
            # Convert y back to full image coordinates
            boxes_info.append((x, y + bottom_start, bw, bh))

    if len(boxes_info) >= 12:
        # Sort: top row first (by y), then left to right (by x)
        boxes_info.sort(key=lambda b: (b[1], b[0]))
        # Take top 6 (row 1) and bottom 6 (row 2)
        row1 = sorted(boxes_info[:6], key=lambda b: b[0])
        row2 = sorted(boxes_info[6:12], key=lambda b: b[0])
        boxes_info = row1 + row2
    else:
        # Fallback to fixed grid
        boxes_info = _fixed_grid_boxes(h, w)

    return [img[y:y+bh, x:x+bw] for x, y, bw, bh in boxes_info]


def _fixed_grid_boxes(h: int, w: int) -> list[tuple[int, int, int, int]]:
    """Fixed grid positions as fallback (calibrated from sample images)."""
    # Row 1: y=580-766, Row 2: y=770-956
    # Columns: 6 equal divisions with small gaps
    col_starts = [10, 225, 439, 654, 868, 1083]
    col_ends = [219, 434, 648, 863, 1077, 1292]
    row_ranges = [(580, 766), (770, 956)]

    boxes = []
    for y_start, y_end in row_ranges:
        for cs, ce in zip(col_starts, col_ends):
            # Scale to actual image dimensions (calibrated for 1303x965)
            sx = int(cs * w / 1303)
            ex = int(ce * w / 1303)
            sy = int(y_start * h / 965)
            ey = int(y_end * h / 965)
            boxes.append((sx, sy, ex - sx, ey - sy))
    return boxes


def _ocr_box(box_img: np.ndarray) -> list[dict]:
    """OCR a single box to extract time ranges.

    Returns list of {"start": "HH:MM", "end": "HH:MM"} dicts.
    """
    import pytesseract

    bh, bw = box_img.shape[:2]

    # Convert to grayscale
    gray = cv2.cvtColor(box_img, cv2.COLOR_BGR2GRAY)

    # Otsu threshold
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Upscale for better OCR
    scaled = cv2.resize(binary, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

    # OCR
    text = pytesseract.image_to_string(scaled, lang="ukr", config="--psm 6")

    return _parse_time_ranges(text)


def _parse_time_ranges(text: str) -> list[dict]:
    """Parse OCR text into structured time ranges."""
    matches = TIME_RANGE_RE.findall(text)
    ranges = []
    for h1, m1, h2, m2 in matches:
        start = f"{int(h1):02d}:{m1}"
        end = f"{int(h2):02d}:{m2}"
        ranges.append({"start": start, "end": end})
    return ranges


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python parser.py <image_path>")
        sys.exit(1)

    result = parse_schedule_image(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
