"""The fictional league: 20 clubs, 25-man squads, coaches, referees and stadiums.

Everything is generated from ``WORLD_SEED`` so every match, slice and season sees the same
league. Club names are coined words checked against the banned-word rules; generated person
names are checked against real people and places by ``earshot.world.checks``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache

from earshot.contract.match import ClubInfo, PlayerInfo, Position, Roster
from earshot.rng import stream
from earshot.world.names import TRADITION_KEYS, PersonName, banned_words_in, draw_name

WORLD_SEED = 2026

SETTINGS = ("press_height", "tempo", "directness", "width", "line_height")
"""The tactical settings every team carries, each on a 0-1 scale."""

STYLES: dict[str, dict[str, float]] = {
    "possession": {
        "press_height": 0.62,
        "tempo": 0.55,
        "directness": 0.25,
        "width": 0.62,
        "line_height": 0.62,
    },
    "pressing": {"press_height": 0.75, "tempo": 0.68, "directness": 0.45, "width": 0.50, "line_height": 0.68},
    "counter": {"press_height": 0.32, "tempo": 0.50, "directness": 0.65, "width": 0.45, "line_height": 0.35},
    "direct": {"press_height": 0.45, "tempo": 0.60, "directness": 0.72, "width": 0.55, "line_height": 0.45},
    "balanced": {"press_height": 0.50, "tempo": 0.50, "directness": 0.48, "width": 0.50, "line_height": 0.50},
}

# (team_id, name, home naming tradition, style, formation, stadium word)
_CLUBS = (
    ("kes", "Kestrels", "anglo", "pressing", "4-3-3", "Kestrel Park"),
    ("cop", "Copperline", "west_african", "possession", "4-2-3-1", "The Smelter"),
    ("tid", "Tidewardens", "nordic", "counter", "4-4-2", "Harbourlight Arena"),
    ("sal", "Saltwings", "hispanic", "possession", "4-3-3", "Saltwing Park"),
    ("gla", "Glasswrights", "francophone", "balanced", "4-2-3-1", "The Kiln"),
    ("riv", "Riverkites", "lusophone", "direct", "4-4-2", "Kite Meadow"),
    ("sto", "Stonecrows", "slavic", "counter", "3-5-2", "Crowstone Arena"),
    ("map", "Mapleguard", "anglo", "balanced", "4-4-2", "Maple Hollow"),
    ("wea", "Weaverbirds", "west_african", "pressing", "4-3-3", "The Loom"),
    ("fli", "Flintlocks", "slavic", "direct", "4-4-2", "Flint Lane"),
    ("cin", "Cinderjacks", "anglo", "direct", "3-4-3", "Ember Yard"),
    ("sil", "Silverstags", "nordic", "possession", "4-2-3-1", "Stag Hill Arena"),
    ("spa", "Sparkwheel", "south_asian", "pressing", "4-3-3", "The Gearhouse"),
    ("dun", "Duneracers", "arabic", "counter", "4-2-3-1", "Sandglass Arena"),
    ("oak", "Oakmasons", "anglo", "balanced", "4-4-2", "Masons Green"),
    ("rav", "Ravenmark", "francophone", "counter", "5-3-2", "Raven Rise"),
    ("bel", "Bellforge", "hispanic", "possession", "4-3-3", "Bell Tower Ground"),
    ("wav", "Wavecutters", "lusophone", "pressing", "4-2-3-1", "Spray Point Arena"),
    ("fer", "Fernbacks", "arabic", "balanced", "3-5-2", "Fern Hollow"),
    ("lan", "Lanterns", "south_asian", "direct", "4-4-2", "Lamplight Arena"),
)

SQUAD_SHAPE: tuple[tuple[Position, int], ...] = (("GK", 3), ("DF", 8), ("MF", 8), ("FW", 6))
_SHIRTS: dict[Position, tuple[int, ...]] = {
    "GK": (1, 13, 31),
    "DF": (2, 3, 4, 5, 6, 15, 22, 26),
    "MF": (7, 8, 10, 14, 16, 18, 20, 24),
    "FW": (9, 11, 17, 19, 23, 27),
}
_SIDES = ("R", "C", "C", "L")
_STARTERS: dict[Position, int] = {"GK": 1, "DF": 5, "MF": 4, "FW": 3}


@dataclass(frozen=True)
class Player:
    player_id: str
    team_id: str
    name: PersonName
    shirt: int
    position: Position
    side: str
    """Preferred side: L, C or R (attacking frame)."""
    rating: float
    """0-1 ability; moves pass success, finishing and duels a little."""

    def info(self) -> PlayerInfo:
        return PlayerInfo(
            player_id=self.player_id,
            team_id=self.team_id,
            name=self.name.full,
            short_name=self.name.surname,
            shirt=self.shirt,
            position=self.position,
        )


@dataclass(frozen=True)
class Club:
    team_id: str
    name: str
    tradition: str
    style: str
    formation: str
    stadium: str
    coach: PersonName
    strength: float
    settings: dict[str, float]
    players: tuple[Player, ...] = field(repr=False)

    def info(self) -> ClubInfo:
        return ClubInfo(
            team_id=self.team_id,
            name=self.name,
            short_name=self.name,
            stadium=self.stadium,
            coach=self.coach.full,
        )


@dataclass(frozen=True)
class World:
    clubs: tuple[Club, ...]
    referees: tuple[PersonName, ...]

    def club(self, team_id: str) -> Club:
        return next(c for c in self.clubs if c.team_id == team_id)

    @property
    def players(self) -> dict[str, Player]:
        return {p.player_id: p for c in self.clubs for p in c.players}

    def roster(self) -> Roster:
        return Roster(
            clubs=[c.info() for c in self.clubs],
            players=[p.info() for c in self.clubs for p in c.players],
        )


def _club(index: int, spec: tuple[str, str, str, str, str, str], taken: set[str]) -> Club:
    team_id, name, tradition, style, formation, stadium = spec
    rng = stream(WORLD_SEED, "club", team_id)
    # Strengths spread evenly from 0.38 to 0.72, shuffled by a fixed permutation of the club list.
    strength = 0.38 + 0.34 * ((index * 7) % 20) / 19
    settings = {
        k: round(min(0.9, max(0.1, v + float(rng.normal(0, 0.05)))), 3) for k, v in STYLES[style].items()
    }
    players = []
    for pos, count in SQUAD_SHAPE:
        for i in range(count):
            # About 60% of a squad shares the club's naming tradition; the rest are drawn evenly.
            trad = tradition if rng.random() < 0.6 else TRADITION_KEYS[int(rng.integers(len(TRADITION_KEYS)))]
            shirt = _SHIRTS[pos][i]
            backup = 0.06 if i >= _STARTERS[pos] else 0.0
            rating = float(min(0.95, max(0.15, rng.normal(strength, 0.08) - backup)))
            players.append(
                Player(
                    player_id=f"{team_id}-{shirt:02d}",
                    team_id=team_id,
                    name=draw_name(rng, trad, taken),
                    shirt=shirt,
                    position=pos,
                    side="C" if pos == "GK" else _SIDES[i % len(_SIDES)],
                    rating=round(rating, 3),
                )
            )
    coach = draw_name(rng, tradition, taken)
    return Club(
        team_id,
        name,
        tradition,
        style,
        formation,
        stadium,
        coach,
        round(strength, 3),
        settings,
        tuple(players),
    )


@cache
def world() -> World:
    taken: set[str] = set()
    clubs = tuple(_club(i, spec, taken) for i, spec in enumerate(_CLUBS))
    rng = stream(WORLD_SEED, "referees")
    referees = tuple(draw_name(rng, TRADITION_KEYS[i % len(TRADITION_KEYS)], taken) for i in range(20))
    for c in clubs:
        for text in (c.name, c.stadium):
            if banned_words_in(text):
                raise ValueError(f"{text!r} uses banned words {banned_words_in(text)}")
    return World(clubs, referees)


@dataclass(frozen=True)
class Fixture:
    index: int
    """0-379 within the season."""
    round: int
    """1-38."""
    home: str
    away: str


@cache
def season_fixtures() -> tuple[Fixture, ...]:
    """A double round-robin (circle method): 38 rounds of 10 matches, 19 home games per club."""
    ids = [c[0] for c in _CLUBS]
    n = len(ids)
    rounds: list[list[tuple[str, str]]] = []
    order = ids[:]
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = order[i], order[n - 1 - i]
            # Alternate the fixed team's venue and flip by round parity so venues balance.
            pairs.append((a, b) if (r + i) % 2 == 0 else (b, a))
        rounds.append(pairs)
        order = [order[0], order[-1], *order[1:-1]]
    second = [[(b, a) for a, b in pairs] for pairs in rounds]
    out: list[Fixture] = []
    for r, pairs in enumerate(rounds + second, start=1):
        for home, away in pairs:
            out.append(Fixture(len(out), r, home, away))
    return tuple(out)


def fixture_for_seed(seed: int) -> Fixture:
    """The fixture a single-match seed plays: season match ``seed`` (1-380), wrapping for other seeds.

    So the development slice (seeds 1-40) is rounds 1-4 of the season, and the held-out seeds
    9001-9040 replay rounds 27-30 with fresh randomness.
    """
    fx = season_fixtures()
    return fx[(seed - 1) % len(fx)]
