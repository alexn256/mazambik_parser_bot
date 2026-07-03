"""Apply manual corrections to krem_parsed.json and build the final
lookup structure: place -> street -> [{houses, queue}]."""
import json
import re

rows = json.load(open("krem_parsed.json"))
corrections = json.load(open("corrections.json"))

WHOLE = "__whole__"
MAJOR_CITIES = ["м. Кременчук", "м. Кобеляки", "м. Глобине", "м. Горішні Плавні"]

# A settlement name can exist in several districts (дільниці) with different
# queues — e.g. two с. Іванівка. Label whole-settlement entries by the nearest
# town so the user can pick their own.
AREA_TOWN = {
    "Кременчуцька": "Кременчук",
    "Великокохнівська": "Велика Кохнівка",
    "Горішньоплавнівська": "Горішні Плавні",
    "Глобинська": "Глобине",
    "Кобеляцька": "Кобеляки",
    "Козельщинська": "Козельщина",
    "Семенівська": "Семенівка",
}


def branch_area(branch: str) -> str:
    word = branch.split()[0]
    return AREA_TOWN.get(word, word)

# Branch-implied default place for rows with streets but no place marker
BRANCH_PLACE = {
    "Горішньоплавнівська": "м. Горішні Плавні",
    "Великокохнівська": None,   # villages vary; keep None -> review
    "Глобинська": None,
    "Кобеляцька": None,
    "Козельщинська": None,
    "Семенівська": None,
    "Кременчуцька": "м. Кременчук",
}

JUNK_STREET_RE = re.compile(
    r"(Абонентськ|^ТП$|^ТП-|^КТП|^ЗТП|^ФТП|^СП-\d|^Д$|^КП |^КВП|^ПНС|^ФКНС|"
    r"^ЦТП|Школа|Гімназія|садок|Виконком|міськрад|каф[ея]|готель|ДЮСШ|"
    r"host|Житловий масив|Зелене господарство|споруда|"
    r"^[А-ЯІЇЄҐ]{2,6}$|"  # all-caps org abbreviations (КНП, ЗОШ, ТДВ, НВП...)
    r"[А-ЯІЇЄҐ][а-яіїєґ]+ [А-ЯІЇЄҐ]\.[А-ЯІЇЄҐ]\.)"  # person names "Кучер П.В."
)


_RU_FOLD = str.maketrans({"ы": "и", "Ы": "И", "э": "е", "Э": "Е",
                          "ё": "е", "Ё": "Е", "ъ": "", "Ъ": ""})


def clean_street_name(name: str) -> str:
    """Normalize a street display name; label bare-number Kremenchuk quarters."""
    name = re.sub(r"\s+", " ", name).strip()
    # Russian-only letters are source typos (no ы/э/ё/ъ in Ukrainian):
    # "Свободы" -> "Свободи", "Столярный" -> "Столярний"
    name = name.translate(_RU_FOLD)
    if name.isdigit():
        name = f"кв. {name}"
    return name


def norm_place(p):
    if not p:
        return p
    p = re.sub(r"\s+", " ", p).strip()
    p = p.replace("с-ще.", "с-ще ").replace("c.", "с.").replace("с.м.т", "смт")
    # unify prefix spacing: "м.Кременчук" -> "м. Кременчук"
    # (longest alternatives first: с-ще/смт before с)
    p = re.sub(r"^(с-ще|смт|сел|м|с)\.?\s*", r"\1. ", p)
    p = p.replace("с-ще.", "с-ще").replace("смт.", "смт")
    # normalize case variants of Горішні Плавні
    if re.fullmatch(r"м\. Горішні [Пп]лавні", p):
        p = "м. Горішні Плавні"
    # strip org tail captured as part of place name
    p = re.sub(r"\s+(ТОВ|ПрАТ|ПАТ|КП|ФОП|ПП|ТДВ|ДП)$", "", p)
    # same settlement written two ways
    if p == "с. Власівка":
        p = "смт Власівка"
    return p


def apply_corrections(row, ops):
    if ops.get("org_only"):
        row["places"] = []
        return
    for op in ops.get("rename_street", []):
        for p in row["places"]:
            for st in p["streets"]:
                if st["name"] == op["from"]:
                    st["name"] = op["to"]
                    if "type" in op:
                        st["type"] = op["type"]
                    if "prepend_houses" in op:
                        st["houses"] = op["prepend_houses"] + st["houses"]
    for name in ops.get("drop_street", []):
        empty_only = name.endswith("@empty")
        target = name.replace("@empty", "")
        for p in row["places"]:
            p["streets"] = [
                st for st in p["streets"]
                if not (st["name"] == target and (not empty_only or not st["houses"]))
            ]
    for op in ops.get("set_houses", []):
        for p in row["places"]:
            for st in p["streets"]:
                if st["name"] == op["street"]:
                    st["houses"] = op["houses"]
    for op in ops.get("add_houses", []):
        for p in row["places"]:
            for st in p["streets"]:
                if st["name"] == op["street"]:
                    st["houses"].extend(h for h in op["houses"] if h not in st["houses"])
    for op in ops.get("fix_house", []):
        for p in row["places"]:
            for st in p["streets"]:
                if st["name"] == op["street"]:
                    st["houses"] = [op["to"] if h == op["from"] else h for h in st["houses"]]
    for op in ops.get("add_street", []):
        target = None
        for p in row["places"]:
            if norm_place(p["place"]) == norm_place(op["place"]):
                target = p
                break
        if target is None:
            target = {"place": op["place"], "streets": []}
            row["places"].append(target)
        target["streets"].append(
            {"name": op["name"], "type": op.get("type"), "houses": op["houses"]})
    for op in ops.get("add_place", []):
        row["places"].append(op)
    if "set_place_all" in ops:
        for p in row["places"]:
            p["place"] = ops["set_place_all"]


for idx_s, ops in corrections.items():
    apply_corrections(rows[int(idx_s)], ops)

# ---- build lookup: place -> street(key) -> entries ----
lookup = {}
skipped_noplace = []
for row in rows:
    queue = row["queue"]
    branch_word = row["branch"].split()[0]
    area = branch_area(row["branch"])
    for p in row["places"]:
        place = norm_place(p["place"]) or BRANCH_PLACE.get(branch_word)
        streets = [st for st in p["streets"]
                   if not JUNK_STREET_RE.search(st["name"])
                   and len(st["name"].strip()) >= 3]
        if place is None:
            if streets:
                skipped_noplace.append((row["queue"], row["branch"][:30],
                                        [s["name"] for s in streets][:5]))
            continue
        pl = lookup.setdefault(place, {})
        if not streets and not p["streets"]:
            # whole settlement entry — but not when the row is a single
            # place + org(s) ("с. Рокитне, юридичні споживачі")
            if len(row["places"]) == 1 and row["orgs"]:
                continue
            pl.setdefault("__whole__", []).append({"queue": queue, "area": area})
            continue
        for st in streets:
            # Merge by street NAME (drop type prefix): the source is
            # inconsistent about вул./просп./проспект. and often loses the
            # prefix entirely, fragmenting one street into several keys.
            key = clean_street_name(st["name"])
            entries = pl.setdefault(key, [])
            entries.append({"houses": st["houses"], "queue": queue, "area": area,
                            **({"alt": st["alt"]} if st.get("alt") else {})})

# merge duplicate entries with same queue for same street
for place, streets in lookup.items():
    for key, entries in streets.items():
        if key == "__whole__":
            continue
        merged = {}
        for e in entries:
            q = e["queue"]
            if q in merged:
                merged[q]["houses"].extend(
                    h for h in e.get("houses", []) if h not in merged[q]["houses"])
            else:
                merged[q] = {"queue": q, "houses": list(e.get("houses", [])),
                             "area": e.get("area")}
        streets[key] = list(merged.values())

# Villages the parser mislabeled: a non-city place whose streets ALL lack
# house numbers and share ONE queue is really a whole settlement in that queue
# (the "streets" are sibling hamlets or unnumbered streets that don't change
# the answer). Multi-queue such places keep their streets, so street selection
# still resolves the queue (e.g. с. Лутовинівка spans 2.1 and 5.1).
for place in list(lookup.keys()):
    if place in MAJOR_CITIES:
        continue
    streets = lookup[place]
    real = [k for k in streets if k != WHOLE]
    if not real:
        continue
    if not all(all(not e.get("houses") for e in streets[k]) for k in real):
        continue
    qset = sorted({e["queue"] for k in real for e in streets[k]})
    if len(qset) != 1:
        continue
    area_by_q = {}
    for k in real:
        for e in streets[k]:
            area_by_q.setdefault(e["queue"], e.get("area"))
    for k in real:
        del streets[k]
    have = {e["queue"] for e in streets.get(WHOLE, [])}
    streets.setdefault(WHOLE, [])
    for q in qset:
        if q not in have:
            streets[WHOLE].append({"queue": q, "area": area_by_q.get(q)})

# drop places that ended up empty (org-only rows)
lookup = {p: s for p, s in lookup.items() if s}

# dedupe whole-settlement entries by (queue, area); drop street-level area
# (only whole entries need it, for same-name-different-district disambiguation)
for place, streets in lookup.items():
    if WHOLE in streets:
        seen, uniq = set(), []
        for e in streets[WHOLE]:
            k = (e["queue"], e.get("area"))
            if k not in seen:
                seen.add(k)
                uniq.append(e)
        streets[WHOLE] = uniq
    for key, entries in streets.items():
        if key == WHOLE:
            continue
        for e in entries:
            e.pop("area", None)

with open("krem_lookup.json", "w", encoding="utf-8") as f:
    json.dump(lookup, f, ensure_ascii=False, indent=1, sort_keys=True)

print("places:", len(lookup))
n_streets = sum(len(v) for v in lookup.values())
print("street keys:", n_streets)
multi_q = 0
for place, streets in lookup.items():
    for key, entries in streets.items():
        if len(entries) > 1:
            multi_q += 1
print("streets with multiple queues:", multi_q)
print()
print("rows with streets but unresolved place:")
for x in skipped_noplace[:15]:
    print("  ", x)
