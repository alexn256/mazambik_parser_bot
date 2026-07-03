"""Parse Kremenchuk rows from skeleton.json into structured addresses.

Every span of the raw text must be consumed by a recognized token type,
or it lands in `leftover` for manual review. Nothing is dropped silently.
"""
import json
import re

# ---------- token regexes (applied at current scan position) ----------

# Organization / legal entity, incl. quoted name (quotes often unbalanced)
ORG_RE = re.compile(
    r"""(?:
        (?:ТОВ|ПрАТ|ПАТ|ЗАТ|ВАТ|АТ|ООО|ФОП|КП|ПП|ДП|СП|РВУ|ТД|СУ-\d+|ТПС-\d+|
           ПРАТ|Садівниче\ товариство|садівниче\ товариство|
           Кооператив|кооп\.?|ЖК|БФ|ГК|Автокооператив)
        [\s,]*
        (?:[«"„''‚,]{1,2}[^"»«]{2,60}["»«]?)?     # optional quoted name
        (?:\s+[А-ЯІЇЄҐ][\w'’\-\.]+){0,4}          # optional trailing words
    )""",
    re.VERBOSE,
)

# Institutions: schools, kindergartens etc.
INST_RE = re.compile(
    r"(?:Школа|школа|Спортшкола|Гімназія|гімназія|Ліцей|ліцей|"
    r"Д[/\\]С|Д[/\\]с|Дитячий садок|Дитсадок|дитячий садок|гуртожитки|гуртожиток|"
    r"Лікарня|лікарня|Амбулаторія|Колонія|колонія|Виправна Колонія|"
    r"ВПУ-\d+|Виконком|водозабір|біостанція|військомат|відділ поліції|"
    r"міська рада|АЗПСМ|Абонентські ТП|ТП-\d+)"
    r"[\s№\d\-,:а-яА-Я()]*?(?=(?:[,.;:]|вул|просп|пров|$))",
)

# Markers meaning "legal consumers" — pure org indicator, safe to drop
LEGAL_RE = re.compile(
    r"юридичні\s+споживачі\.?|юр\.?\s*спож\.?|юридичний\s+споживач|"
    r"філія\s+[А-ЯІЇЄҐ][\w'’\-]+(?:\s+[а-яіїєґ\w'’\-]+)?|"
    r"гаражі\s*\"[^\"]+\"?|кіоск[^,;]*|сквер\s+[А-ЯІЇЄҐ][\w'’\-]*\.?|"
    r"ПІДПРИЄМСТВО\s*\"[^\"]+\"?",
    re.IGNORECASE,
)

# Place: м. / с. / смт / сел. + name; allow initial like "В.Кринки";
# latin "c" typo; "с.м.т."; second word may be lowercase ("Горішні плавні")
PLACE_RE = re.compile(
    r"(м|с\.м\.т|с|c|смт|сел|с-ще)\s*[\.\s]\s*"
    r"((?:[А-ЯІЇЄҐ]\.\s*)?[А-ЯІЇЄҐ][\w'’\-]+"
    r"(?:\s+(?:[А-ЯІЇЄҐ][\w'’\-]+|плавні|Плавні))?)"
)

# Street prefix. Short ambiguous ones (пл, пр, кв) require a dot.
STREET_PREFIX_RE = re.compile(
    r"(вулиця|вул\.?|провулок|пров\.?|проспект|просп\.?|пр-т|проїзд|"
    r"бульвар|бульв\.?|б-\s?р|площа|квартал|кварт\.?|кв-л|узвіз|шосе|дорога|мкр|"
    r"тупіки|тупік|тупик|туп\.?|пл\.|пр\.|кв\.|"
    r"Площа|Вул\.?|Провулок|Пров\.?|Проспект|Просп\.?|Тупік|Туп\.|Кв\.)\s*\.?\s*",
)

# Street names that start with a number: "29 Вересня", "1-го Травня"
NUM_STREET_RE = re.compile(
    r"(\d+(?:-го)?\s+(?:Вересня|Жовтня|Листопада|Грудня|Січня|Лютого|"
    r"Березня|Квітня|Травня|Червня|Липня|Серпня))"
)

# Street name: 1-3 capitalized words (allow lowercase connectors and initials)
STREET_NAME_RE = re.compile(
    r"([А-ЯІЇЄҐ\d][\w'’\-\.]*(?:\s+[А-ЯІЇЄҐа-яіїєґ][\w'’\-\.]*){0,3})"
)

# House token: 26, 26а, 11/1, 103а-г, 23 - 39/43 (range), "№ 5",
# optional corpus "к-1"/"корп.4" / entry "ввід1"/"ввод№2" suffix.
# Letter suffix must not be glued to more letters ("96вул" is not house 96в).
HOUSE_RE = re.compile(
    r"((?:№\s*)?\d+(?:[\-/]?[а-яА-ЯіІїЇєЄґҐ](?![а-яА-Яa-z]))?(?:/\d+[а-яА-Я]?)?"
    r"(?:\s*-\s*\d+[а-яА-Я]?(?:/\d+[а-яА-Я]?)?)?"
    r"(?:\s*(?:к-\d+|корп\.?\s*\d+|вв[іо]д\s*№?\s*\d+))?)"
)

# Parenthesized alternate street name: "(Пушкіна)" — keep street context
PAREN_RE = re.compile(r"\([^)]{0,60}\)?")

SEP_RE = re.compile(r"[\s,;\.\:—–\"«»„']+")


def parse_row(raw: str) -> dict:
    s = re.sub(r"\s+", " ", raw).strip()
    pos = 0
    n = len(s)

    places = []          # [{place, streets: [{name, houses}]}]
    orgs = []
    leftover = []
    cur_place = None
    cur_street = None

    def ensure_place():
        nonlocal cur_place
        if cur_place is None:
            cur_place = {"place": None, "streets": []}
            places.append(cur_place)
        return cur_place

    while pos < n:
        m = SEP_RE.match(s, pos)
        if m:
            pos = m.end()
            continue

        # stray ")" or lone "№" before a street prefix — skip
        if s[pos] == ")":
            pos += 1
            continue
        if s[pos] == "№" and not re.match(r"№\s*\d", s[pos:]):
            pos += 1
            continue

        m = LEGAL_RE.match(s, pos)
        if m:
            orgs.append(m.group(0))
            pos = m.end()
            continue

        # numbered street names like "29 Вересня" (before house matching)
        m = NUM_STREET_RE.match(s, pos)
        if m:
            cur_street = {"name": m.group(1), "type": "вул", "houses": []}
            ensure_place()["streets"].append(cur_street)
            pos = m.end()
            continue

        m = ORG_RE.match(s, pos)
        if m and len(m.group(0).strip()) > 2:
            orgs.append(m.group(0).strip(" ,"))
            pos = m.end()
            cur_street = None
            continue

        m = INST_RE.match(s, pos)
        if m:
            orgs.append(m.group(0).strip(" ,"))
            pos = m.end()
            cur_street = None
            continue

        m = PAREN_RE.match(s, pos)
        if m:
            # parenthesized alternate name — attach to current street as note
            if cur_street is not None:
                cur_street.setdefault("alt", m.group(0).strip("() "))
            pos = m.end()
            continue

        m = PLACE_RE.match(s, pos)
        if m:
            cur_place = {"place": f"{m.group(1)}. {m.group(2)}", "streets": []}
            places.append(cur_place)
            cur_street = None
            pos = m.end()
            continue

        m = STREET_PREFIX_RE.match(s, pos)
        if m:
            prefix = m.group(1).rstrip(".").lower()
            pos = m.end()
            nm = STREET_NAME_RE.match(s, pos)
            if nm:
                name = nm.group(1).strip(" ,.-")
                cur_street = {"name": name, "type": prefix, "houses": []}
                ensure_place()["streets"].append(cur_street)
                pos = nm.end()
            continue

        m = HOUSE_RE.match(s, pos)
        if m and cur_street is not None:
            cur_street["houses"].append(re.sub(r"\s*-\s*", "-", m.group(1)))
            pos = m.end()
            continue

        # bare street name (no prefix): capitalized word(s)
        m = STREET_NAME_RE.match(s, pos)
        if m and re.match(r"[А-ЯІЇЄҐ]", s[pos]):
            name = m.group(1).strip(" ,.-")
            cur_street = {"name": name, "houses": []}
            ensure_place()["streets"].append(cur_street)
            pos = m.end()
            continue

        # nothing matched: consume until next separator into leftover
        nxt = re.search(r"[,;]", s[pos:])
        end = pos + nxt.start() if nxt else n
        chunk = s[pos:end].strip()
        if chunk:
            leftover.append(chunk)
        pos = end + 1

    return {"places": places, "orgs": orgs, "leftover": leftover}


def main():
    records = json.load(open("skeleton.json"))
    krem = [r for r in records if "Кременчуцьк" in (r["branch"] or "")]

    out = []
    stats = {"total": 0, "with_leftover": 0, "org_only": 0}
    for r in krem:
        parsed = parse_row(r["raw"])
        stats["total"] += 1
        has_addr = any(p["streets"] or p["place"] for p in parsed["places"])
        if not has_addr:
            stats["org_only"] += 1
        if parsed["leftover"]:
            stats["with_leftover"] += 1
        out.append({
            "queue": f"{r['queue']}.{r['sub']}",
            "branch": r["branch"],
            "n": r["n"],
            "raw": r["raw"],
            **parsed,
        })

    print(stats)
    with open("krem_parsed.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("saved krem_parsed.json")


if __name__ == "__main__":
    main()
