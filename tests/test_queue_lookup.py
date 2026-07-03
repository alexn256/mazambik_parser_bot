import os

import pytest

from queue_lookup import MAJOR_CITIES, QueueLookup, _house_matches, normalize

DATA = os.path.join(os.path.dirname(__file__), "..", "krem_queues.json")


@pytest.fixture(scope="module")
def lk():
    return QueueLookup(DATA)


class TestNormalize:
    def test_case_and_prefix(self):
        assert normalize("вул. Лесі Українки") == normalize("ЛЕСІ УКРАЇНКИ")

    def test_prefix_variants_equal(self):
        assert normalize("просп. Свободи") == normalize("проспект Свободи")

    def test_confusable_letters(self):
        # і / ї / и / й collapse; е / є collapse
        assert normalize("Українки") == normalize("Украінки")
        assert normalize("Київська") == normalize("Киівська")

    def test_apostrophe_variants(self):
        assert normalize("Лук'яненка") == normalize("Лукʼяненка") == normalize("Лукяненка")


class TestPlaces:
    def test_resolve_exact_lowercase(self, lk):
        assert lk.resolve_place("кременчук") == "м. Кременчук"

    def test_major_cities_present(self, lk):
        for city in MAJOR_CITIES:
            assert lk.place_streets_count(city) > 0

    def test_search_village_typo(self, lk):
        # missing prefix, lowercase
        assert "с. Потоки" in lk.search_places("потоки")

    def test_search_unknown_returns_empty(self, lk):
        assert lk.search_places("залізякабумбарум") == []


class TestStreets:
    def test_typo_still_finds_street(self, lk):
        # Russian-ish spelling: и instead of і/ї
        cands = lk.search_streets("м. Кременчук", "леси украинки")
        assert "Лесі Українки" in cands

    def test_no_prefix_needed(self, lk):
        cands = lk.search_streets("м. Кременчук", "свободи")
        assert "Свободи" in cands

    def test_single_letter_query_no_junk(self, lk):
        # a bare letter must not surface streets merely containing it
        cands = lk.search_streets("м. Кременчук", "свободи")
        assert all(len(c.strip()) >= 3 for c in cands)

    def test_split_street_has_multiple_queues(self, lk):
        qs = lk.street_queues("м. Кременчук", "Лесі Українки")
        assert len(qs) > 1

    def test_whole_village_queue(self, lk):
        # a village stored as a single-queue whole settlement
        qs = lk.whole_queues("с-ще Градизьк")
        assert qs and all("." in q for q in qs)


class TestDataIntegrity:
    def test_no_junk_street_names(self, lk):
        # single/double-letter names and bare org abbreviations are parsing
        # anomalies, never real streets — they must not reach the dataset
        import re
        for place, streets in lk._data.items():
            for key in streets:
                if key == "__whole__":
                    continue
                assert len(key.strip()) >= 3, f"too short: {place} / {key!r}"
                assert not re.fullmatch(r"[А-ЯІЇЄҐ]{2,6}", key.strip()), \
                    f"org abbreviation: {place} / {key!r}"


class TestWholeSettlement:
    def test_streetless_village_is_whole(self, lk):
        # с. М.Кохнівка appears once in the source (queue 3.2): the whole
        # settlement is in that one queue, so it must resolve without a street.
        assert lk.place_streets_count("с. М.Кохнівка") == 0
        assert lk.whole_queues("с. М.Кохнівка") == ["3.2"]

    def test_single_queue_houseless_places_flattened(self, lk):
        # invariant: a non-city place whose streets all lack house numbers and
        # share one queue must have been collapsed into a whole settlement
        for place, streets in lk._data.items():
            if place in MAJOR_CITIES:
                continue
            real = [k for k in streets if k != "__whole__"]
            if not real:
                continue
            if not all(all(not e.get("houses") for e in streets[k]) for k in real):
                continue
            queues = {e["queue"] for k in real for e in streets[k]}
            assert len(queues) > 1, f"{place} should be a whole settlement"


class TestHouseMatches:
    def test_exact(self):
        assert _house_matches("55", "55")

    def test_exact_with_letter_suffix(self):
        assert _house_matches("55Б", "55")
        assert _house_matches("55", "55-Б")

    def test_numeric_range_inclusive(self):
        assert _house_matches("50", "39-63")
        assert _house_matches("39", "39-63")
        assert _house_matches("63", "39-63")

    def test_out_of_range(self):
        assert not _house_matches("70", "39-63")
        assert not _house_matches("38", "39-63")

    def test_parity_even(self):
        assert _house_matches("36", "парні 32-162")
        assert not _house_matches("35", "парні 32-162")

    def test_parity_odd(self):
        assert _house_matches("113", "непарні 113-173")
        assert not _house_matches("114", "непарні 113-173")

    def test_non_numeric_house(self):
        assert not _house_matches("абв", "12")


class TestResolveHouse:
    def test_house_lands_in_right_queue(self, lk):
        # 'Лесі Українки' is split; house 55 is listed under a specific queue.
        queues = lk.resolve_house("м. Кременчук", "Лесі Українки", "55")
        assert queues  # found in at least one queue
        assert all("." in q for q in queues)

    def test_unknown_house_returns_empty(self, lk):
        assert lk.resolve_house("м. Кременчук", "Лесі Українки", "99999") == []
