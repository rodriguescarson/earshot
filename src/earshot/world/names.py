"""Fictional person names by naming tradition, and the banned-word rules for club names.

Given names are common first names (not identifying on their own). Surnames are built from a
prefix and a suffix morpheme of the same tradition, so they read naturally without copying a
real person. Every generated full name is later filtered against real footballers, referees and
managers (Wikidata) and real places (GeoNames); see ``earshot.world.checks``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Tradition:
    key: str
    given: tuple[str, ...]
    prefixes: tuple[str, ...]
    suffixes: tuple[str, ...]


def _t(key: str, given: str, prefixes: str, suffixes: str) -> Tradition:
    return Tradition(key, tuple(given.split()), tuple(prefixes.split()), tuple(suffixes.split()))


TRADITIONS: dict[str, Tradition] = {
    t.key: t
    for t in (
        _t(
            "anglo",
            "Callum Ewan Jody Rhys Owen Theo Mason Reece Kieran Lewis Toby Ellis Ronan Jamie Alfie Corey",
            "Ash Brad Carr Dun Fen Gar Hal Kel Lang Mar Pen Ross Shel Thorn Wex Yar",
            "worth ley mere by well ard ing ock man er ridge cott",
        ),
        _t(
            "hispanic",
            "Mateo Iker Thiago Bruno Dario Emilio Gael Hugo Joaquin Lisandro Nahuel Rodrigo Santino Tomas "
            "Valentin Ulises",
            "Var Mon Cal Bel Ord Sal Ter Quin Alv Bar Cas Fer Gal Lor Mir Rib",
            "era illa ado edo ana ales ero oza ino ente aro uela",
        ),
        _t(
            "lusophone",
            "Joao Rafael Caio Davi Enzo Igor Luan Murilo Otavio Renan Tiago Vitor",
            "Ar Bra Cor Fon Gou Lob Mac Nog Per Qua Ser Tav",
            "anha eira elo inho oso ais oto ado",
        ),
        _t(
            "francophone",
            "Bastien Corentin Elouan Florian Gaetan Jordan Killian Loic Maxence Nolan Quentin Romain Tanguy "
            "Yanis",
            "Bou Char Del Fav Gir Lam Mor Pel Roch Tess Vall Lec",
            "ard eau ier on ault elle et ain ot eux",
        ),
        _t(
            "west_african",
            "Adewale Chidi Emeka Femi Ikenna Kwabena Kofi Obinna Seun Tunde Yaw Kwame Ebuka Dayo",
            "Ade Oko Nwo Ola Afo Eze Iwu Obi Asa Ogu Ama Kel",
            "yemi nkwo bisi for chukwu wale lade nna toye mah gbe du",
        ),
        _t(
            "arabic",
            "Karim Yassin Hamza Ilyas Rayan Samir Tarek Walid Zakaria Adel Bilal Nabil Hicham Anas",
            "Bou Ham Kha Mans Nas Sab Tah Zer Fadl Jab Mak Raf",
            "ari idi oun ani ouri adi ini ane ir ali oud ira",
        ),
        _t(
            "south_asian",
            "Arjun Dev Ishaan Kabir Nikhil Rohan Sahil Tanvir Vikram Aarav Imran Harjit Pranav Ravi",
            "Bha Cha Desh Kap Mal Nar Pat Rav Sha Tal Vas Jai",
            "wani ndra kar ore wal ana deo iya ani nath esh var",
        ),
        _t(
            "slavic",
            "Bogdan Damir Filip Goran Ivo Luka Marko Nikola Petar Stipe Vuk Zoran Darko Mirko",
            "Kov Mil Rad Pet Jan Stan Vel Dra Bor Zel Kra Lub",
            "ovic enko ski ic ar ek ov ec anik usic",
        ),
        _t(
            "nordic",
            "Anders Birk Eskil Halvar Jonas Leif Magnus Oskar Sindre Tobias Viggo Elias",
            "Berg Dahl Hol Lind Nor Sand Vik Ask Ek Fal Hed Lund",
            "gren qvist mark strand holm sen vold dal rud",
        ),
    )
}

TRADITION_KEYS: tuple[str, ...] = tuple(TRADITIONS)

BANNED_WORDS = frozenset(
    {
        # Words that make a club name evoke a real club or a real place (BUILD-BRIEF section 0).
        "city", "united", "rovers", "albion", "town", "county", "athletic", "wanderers", "villa",
        "hotspur", "palace", "forest", "rangers", "celtic", "wednesday", "orient", "argyle",
        "borough", "dynamo", "real", "sporting", "olympic", "inter", "harriers", "magpies",
        "foxes", "wolves", "hornets", "canaries", "owls", "eagles", "seagulls", "bees", "cherries",
        "potters", "saints", "hammers", "toffees", "gunners", "blades", "tigers", "robins", "spurs",
        "north", "south", "east", "west", "london", "manchester", "liverpool", "fc", "afc",
    }
)  # fmt: skip

PLACE_SUFFIXES = (
    "ton", "ham", "wick", "ford", "field", "bury", "mouth", "chester", "pool", "burgh", "vale",
    "moor", "gate", "bridge", "port", "shire", "borough", "stead", "caster",
)  # fmt: skip
"""Endings that make a coined word read like an English place name."""


def banned_words_in(name: str) -> list[str]:
    """Banned words found in a club, stadium or team name (case-insensitive, whole words)."""
    words = re.findall(r"[a-z]+", name.lower())
    hits = [w for w in words if w in BANNED_WORDS]
    hits += [f"*{s}" for w in words for s in PLACE_SUFFIXES if w.endswith(s) and len(w) > len(s) + 2]
    return hits


@dataclass(frozen=True)
class PersonName:
    given: str
    surname: str
    tradition: str
    prefix: str
    suffix: str

    @property
    def full(self) -> str:
        return f"{self.given} {self.surname}"


def surname(prefix: str, suffix: str) -> str:
    joined = prefix + suffix
    # Avoid doubled letters at the seam reading as a typo ("Kovvic"), but keep "ll", "ss", "nn".
    if prefix[-1].lower() == suffix[0].lower() and suffix[0].lower() not in "lsnr":
        joined = prefix + suffix[1:]
    return joined[0].upper() + joined[1:]


def draw_name(rng: np.random.Generator, tradition: str, taken: set[str]) -> PersonName:
    """Draw a name from ``tradition`` whose full name is not in ``taken`` (adds it)."""
    t = TRADITIONS[tradition]
    for _ in range(500):
        g = t.given[int(rng.integers(len(t.given)))]
        p = t.prefixes[int(rng.integers(len(t.prefixes)))]
        s = t.suffixes[int(rng.integers(len(t.suffixes)))]
        n = PersonName(g, surname(p, s), tradition, p, s)
        if n.full not in taken and n.surname not in _SURNAME_BLOCKLIST:
            n = _avoid_bad_reading(n, t, taken)
            taken.add(n.full)
            return n
    raise RuntimeError(f"name space exhausted for {tradition}")


def _avoid_bad_reading(n: PersonName, t: Tradition, taken: set[str]) -> PersonName:
    """Swap the suffix for the next one in the tradition until the surname reads cleanly.

    Deterministic and draws no random numbers, so blocking a surname never shifts the rest of
    the generated league (ratings come from the same stream).
    """
    if n.surname not in BAD_READINGS:
        return n
    i = t.suffixes.index(n.suffix)
    for k in range(1, len(t.suffixes)):
        s = t.suffixes[(i + k) % len(t.suffixes)]
        alt = PersonName(n.given, surname(n.prefix, s), n.tradition, n.prefix, s)
        if (
            alt.surname not in BAD_READINGS
            and alt.surname not in _SURNAME_BLOCKLIST
            and alt.full not in taken
        ):
            return alt
    raise RuntimeError(f"no clean surname for {n.full}")


_SURNAME_BLOCKLIST = frozenset({"Mareo", "Salado", "Barado"})
"""Surnames that read as ordinary words in one of the four languages (redrawn)."""

BAD_READINGS = frozenset({"Hamir", "Khaini", "Khari", "Bhaiya", "Chakar"})
"""Surnames found to read badly in Arabic or Hindi when the roster tables were written:
Hamir (Arabic "donkeys"), Khaini (close to Arabic "traitor"), Khari (close to an Arabic
vulgarity), Bhaiya (Hindi "brother"), Chakar (Hindi "servant"). Replaced deterministically."""
