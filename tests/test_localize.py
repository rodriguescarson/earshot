import re

import pytest

from earshot.contract.line import Language
from earshot.world.localize import roster
from earshot.world.names import BAD_READINGS

ARABIC = re.compile(r"^[؀-ۿ ]+$")
DEVANAGARI = re.compile(r"^[ऀ-ॿ ]+$")


@pytest.mark.parametrize(("lang", "script"), [(Language.AR, ARABIC), (Language.HI, DEVANAGARI)])
def test_every_name_has_a_rendering_in_the_script(lang, script):
    r = roster(lang)
    assert len(r.players) == 500
    assert len(r.clubs) == 20
    for p in r.players:
        assert script.match(p.name), p
        assert script.match(p.short_name), p
        assert p.short_name in p.name
    for c in r.clubs:
        assert script.match(c.short_name), c


def test_no_triple_letters_or_doubled_vowel_signs():
    for p in roster(Language.AR).players:
        assert not re.search(r"(.)\1\1", p.name), p.name
    for p in roster(Language.HI).players:
        assert not re.search(r"[ा-ौ]{2}", p.name), p.name


def test_latin_languages_keep_the_latin_roster_and_ids_never_change():
    en, es, ar = roster(Language.EN), roster(Language.ES), roster(Language.AR)
    assert en == es
    assert [p.player_id for p in en.players] == [p.player_id for p in ar.players]
    assert [p.shirt for p in en.players] == [p.shirt for p in ar.players]


def test_names_that_read_badly_are_gone():
    surnames = {p.short_name for p in roster(Language.EN).players}
    assert not surnames & BAD_READINGS
