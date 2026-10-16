"""Offline parts of the name checks (the live Wikidata run writes reports/names-check.json)."""

import json
from pathlib import Path

from earshot.world import checks


def test_queries_ask_for_football_people_and_settlements():
    q = checks.people_query(['Owen "Q" Halworth'])
    assert '"Owen \\"Q\\" Halworth"@en' in q
    for occ in checks.FOOTBALL_OCCUPATIONS:
        assert f"wd:{occ}" in q
    assert "wd:Q2736" in q
    assert "wd:Q486972" in checks.places_query(["Kestrels"])


def test_every_generated_person_and_club_is_in_scope():
    people = checks.person_names()
    assert len(people) == 540  # 500 players, 20 coaches, 20 referees
    words = checks.place_words()
    assert "Kestrels" in words
    assert "Kestrel Park" in words


def test_committed_report_found_no_real_football_people():
    report = json.loads((Path(__file__).parent.parent / "reports" / "names-check.json").read_text())
    assert report["people_checked"] == 540
    assert report["person_matches"] == []
    assert report["delta_check"]["person_matches"] == []
