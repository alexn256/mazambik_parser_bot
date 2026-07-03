"""Address -> power-cut queue lookup for the Kremenchuk branch.

Data: krem_queues.json — {place: {street_name: [{houses, queue, alt?}], "__whole__": [...]}}
Street keys are bare names like "Лесі Українки" (type prefix dropped upstream
because the source is inconsistent about it); whole-settlement entries live
under the "__whole__" key.

Search is fuzzy and typo-tolerant: users rarely spell street names exactly,
so we normalize aggressively (case, і/ї/и/й, е/є, apostrophes, type prefix)
and return ranked candidates for the user to confirm with a button.
"""
import json
import re
from difflib import SequenceMatcher

MAJOR_CITIES = ["м. Кременчук", "м. Кобеляки", "м. Глобине", "м. Горішні Плавні"]

WHOLE = "__whole__"

_STREET_PREFIX = re.compile(
    r"^(вулиця|вул|проспект|просп|провулок|пров|бульвар|бульв|б-?\s?р|"
    r"площа|пл|тупік|тупик|туп|проїзд|мікрорайон|мкр|шосе|дорога|проспект|пр)"
    r"\.?\s*",
)
_PLACE_PREFIX = re.compile(r"^(смт|м|с-ще|сел|с)\.?\s*")


def normalize(s: str) -> str:
    """Fold a street/place name to a typo-tolerant search key."""
    s = s.lower().strip()
    s = _STREET_PREFIX.sub("", s)
    for ch in "`'’ʼ‚":
        s = s.replace(ch, "")
    # collapse visually/aurally confusable Ukrainian letters
    s = (s.replace("ї", "і").replace("и", "і").replace("й", "і")
           .replace("є", "е").replace("ґ", "г"))
    s = re.sub(r"[\s\-]+", " ", s)
    return s.strip()


def _place_key(place: str) -> str:
    return normalize(_PLACE_PREFIX.sub("", place))


class QueueLookup:
    def __init__(self, path: str):
        with open(path, encoding="utf-8") as f:
            self._data: dict = json.load(f)
        # place search index: normalized name -> canonical place
        self._place_index = {_place_key(p): p for p in self._data}
        # per-place street index: normalized street name -> [street_key...]
        self._street_index: dict[str, dict[str, list[str]]] = {}
        for place, streets in self._data.items():
            idx: dict[str, list[str]] = {}
            for key in streets:
                if key == WHOLE:
                    continue
                idx.setdefault(normalize(key), []).append(key)
                for entry in streets[key]:
                    alt = entry.get("alt")
                    if alt:
                        idx.setdefault(normalize(alt), []).append(key)
            self._street_index[place] = idx

    # ---- places ----------------------------------------------------------

    def resolve_place(self, query: str) -> str | None:
        """Exact (normalized) place match, else None."""
        return self._place_index.get(_place_key(query))

    def search_places(self, query: str, limit: int = 8) -> list[str]:
        q = _place_key(query)
        if not q:
            return []
        scored = []
        for norm, place in self._place_index.items():
            score = _match_score(q, norm)
            if score > 0:
                scored.append((score, place))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [p for _, p in scored[:limit]]

    def place_streets_count(self, place: str) -> int:
        return len([k for k in self._data.get(place, {}) if k != WHOLE])

    def whole_queues(self, place: str) -> list[str]:
        entries = self._data.get(place, {}).get(WHOLE, [])
        return sorted({e["queue"] for e in entries})

    # ---- streets ---------------------------------------------------------

    def search_streets(self, place: str, query: str, limit: int = 8) -> list[str]:
        idx = self._street_index.get(place, {})
        q = normalize(query)
        if not q:
            return []
        scored = []
        seen = set()
        for norm, keys in idx.items():
            score = _match_score(q, norm)
            if score <= 0:
                continue
            for key in keys:
                if key in seen:
                    continue
                seen.add(key)
                scored.append((score, key))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [k for _, k in scored[:limit]]

    def street_entries(self, place: str, street_key: str) -> list[dict]:
        return self._data.get(place, {}).get(street_key, [])

    def street_queues(self, place: str, street_key: str) -> list[str]:
        return sorted({e["queue"] for e in self.street_entries(place, street_key)})

    # ---- houses ----------------------------------------------------------

    def resolve_house(self, place: str, street_key: str, house: str) -> list[str]:
        """Return queues whose house list contains `house`.

        Empty list means the house was not found — caller should fall back to
        showing every queue present on the street rather than guessing.
        """
        matched = []
        for entry in self.street_entries(place, street_key):
            if any(_house_matches(house, tok) for tok in entry.get("houses", [])):
                matched.append(entry["queue"])
        return sorted(set(matched))


def _match_score(q: str, target: str) -> float:
    """0 = no match; higher = better. Prefix > substring > fuzzy ratio."""
    if not target:
        return 0.0
    if target == q:
        return 3.0
    if target.startswith(q) or q.startswith(target):
        return 2.5
    # substring only when the shorter side is meaningful (avoid "б" ⊂ "свободи")
    if min(len(q), len(target)) >= 3 and (q in target or target in q):
        return 2.0
    ratio = SequenceMatcher(None, q, target).ratio()
    return 1.0 + ratio if ratio >= 0.6 else 0.0


def _house_num(h: str) -> int | None:
    m = re.match(r"\s*(\d+)", h)
    return int(m.group(1)) if m else None


def _house_matches(user_house: str, token: str) -> bool:
    un = _house_num(user_house)
    if un is None:
        return False
    tok = token.strip()
    parity = None
    if tok.startswith("парні"):
        parity, tok = 0, tok[len("парні"):].strip()
    elif tok.startswith("непарні"):
        parity, tok = 1, tok[len("непарні"):].strip()

    rng = re.match(r"(\d+)\s*-\s*(\d+)\s*$", tok)
    if rng:
        lo, hi = int(rng.group(1)), int(rng.group(2))
        if lo > hi:
            lo, hi = hi, lo
        if lo <= un <= hi:
            return parity is None or un % 2 == parity
        return False

    tn = _house_num(tok)
    return tn is not None and tn == un
