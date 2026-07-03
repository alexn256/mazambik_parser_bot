# Queue data pipeline (PDF → krem_queues.json)

Regenerates `krem_queues.json` (the address → power-cut-queue lookup used by
the "🔍 Яка у мене черга?" bot feature) from the official Poltavaoblenergo
schedule PDF.

The pipeline is **offline** — it is not part of the bot runtime. Its extra
dependencies (`pdfplumber`) are not in `requirements.txt`.

## Steps

1. Download the current PDF from https://www.poe.pl.ua/ and put its path into
   `extract_skeleton.py` (`PDF = ...`).
2. Run in order:
   ```
   python extract_skeleton.py   # PDF tables → skeleton.json (queue/branch/rows)
   python parse_addresses.py     # skeleton.json → krem_parsed.json (place/street/houses)
   python build_final.py         # + corrections.json → krem_lookup.json
   ```
3. Copy `krem_lookup.json` to the repo root as `krem_queues.json`.

## Files

- `extract_skeleton.py` — pulls table rows out of the PDF, tracks the current
  queue/subqueue/branch, and stitches rows split across page breaks.
- `parse_addresses.py` — parses each Kremenchuk-branch row's free text into
  places/streets/houses. Anything it can't classify goes to `leftover`.
- `corrections.json` — manual fixes for the ~50 rows that needed review
  (mislabeled streets, org-only rows, split settlements).
- `build_final.py` — applies corrections, drops legal entities and parsing
  anomalies, merges fragmented streets, and emits the final lookup.

Only the Kremenchuk branch (`Кременчуцька філія` + its дільниці) is kept;
legal/industrial consumers are dropped — the feature is for residential users.
