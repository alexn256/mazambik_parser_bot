"""Extract the full skeleton of the HPV PDF: queue -> subqueue -> branch -> rows."""
import json
import re

import pdfplumber

PDF = "/home/alexn256/dev/projects/mazambik_parser_bot/HPV-26-lito-zminy-8-kvitnia-sajt-13.04.pdf"

QUEUE_WORDS = {
    "Перша": 1, "Друга": 2, "Третя": 3,
    "Четверта": 4, "П'ята": 5, "Шоста": 6,
}

# e.g. "Перша черга І підчерга" / "Шоста черга ІІ підчерга"
SUBQUEUE_RE = re.compile(
    r"^(Перша|Друга|Третя|Четверта|П['’]ята|Шоста)\s+черга\s+(І|ІІ|I|II)\s+підчерга\s*$"
)
QUEUE_RE = re.compile(r"^(Перша|Друга|Третя|Четверта|П['’]ята|Шоста)\s+черга\s*$")

records = []
current = {"queue": None, "sub": None, "branch": None}

with pdfplumber.open(PDF) as pdf:
    for pageno, page in enumerate(pdf.pages, 1):
        for table in page.extract_tables():
            for row in table:
                cells = [(c or "").replace("\n", " ").strip() for c in row]
                # Normalize: drop empty trailing cells
                non_empty = [c for c in cells if c]
                if not non_empty:
                    continue
                first = cells[0]

                # Row with number + text
                if re.fullmatch(r"\d{1,3}", first) and len(cells) > 1 and cells[1]:
                    records.append({
                        "page": pageno,
                        "queue": current["queue"],
                        "sub": current["sub"],
                        "branch": current["branch"],
                        "n": int(first),
                        "raw": cells[1],
                    })
                    continue

                # Unnumbered fragment: row cell spanning a page break
                # (first cell empty, substantial text in second cell)
                if (not first and len(cells) > 1 and cells[1]
                        and len(cells[1]) > 30
                        and not re.match(r"^(Назви|Графік)", cells[1])):
                    records.append({
                        "page": pageno,
                        "queue": current["queue"],
                        "sub": current["sub"],
                        "branch": current["branch"],
                        "n": None,
                        "raw": cells[1],
                    })
                    continue

                text = " ".join(non_empty)
                text_clean = re.sub(r"\s+", " ", text).strip()

                m = SUBQUEUE_RE.match(text_clean)
                if m:
                    q = m.group(1).replace("’", "'")
                    current["queue"] = QUEUE_WORDS[q]
                    current["sub"] = 1 if m.group(2) in ("І", "I") else 2
                    continue
                m = QUEUE_RE.match(text_clean)
                if m:
                    current["queue"] = QUEUE_WORDS[m.group(1).replace("’", "'")]
                    continue
                if "філі" in text_clean or "дільниця" in text_clean:
                    current["branch"] = text_clean
                    continue
                # other headers ignored (title row, № з/п)

# Merge continuation records: raw starting with a bare number list or
# lowercase fragment continues the previous record of the same section.
merged = []
for r in records:
    cont = re.match(r"^\d+[а-яА-Я]?[,/]", r["raw"])
    if (merged and cont
            and merged[-1]["queue"] == r["queue"]
            and merged[-1]["sub"] == r["sub"]
            and merged[-1]["branch"] == r["branch"]):
        merged[-1]["raw"] += " " + r["raw"]
        continue
    merged.append(r)
records = merged

print("total records:", len(records))

# Sanity: all queue/sub combos present?
combos = sorted({(r["queue"], r["sub"]) for r in records})
print("combos:", combos)

branches = sorted({r["branch"] for r in records if r["branch"]})
print("branches:", len(branches))
for b in branches:
    print("  -", b)

with open("skeleton.json", "w", encoding="utf-8") as f:
    json.dump(records, f, ensure_ascii=False, indent=1)
print("saved skeleton.json")
