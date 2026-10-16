"""Cited reference values for the realism gate (G3) and the realism report.

Every value has its source URL, the access date and a short verbatim quote. ``verified`` is True
when the quote was fetched first-hand while writing this file (2026-10-16); False marks a value
gathered by the research pass (an AI subagent) and not re-fetched. Those values are reported
but never gate.

Band rule (pre-registered in EVALUATION.md): a gated metric passes if the full-season mean lies
inside the span of its verified season values, widened by 10% for counts and minutes, or by 3
percentage points for rates.
"""

from __future__ import annotations

from dataclasses import dataclass

ACCESSED = "2026-10-16"

AN_BIP = "https://theanalyst.com/articles/premier-league-ball-in-play-are-we-seeing-less-football-2025-26"
PL_TRENDS = "https://www.premierleague.com/news/4271974"
PL_SEASON = "https://www.premierleague.com/news/4031506"
PL_PASS = "https://www.premierleague.com/en/news/4426039"
AN_CARDS = "https://theanalyst.com/articles/premier-league-2024-25-data-trends-stats"
AN_HOME = "https://theanalyst.com/articles/premier-league-home-wins-covid-levels"
SIEGLE_LAMES_2012 = "https://doi.org/10.1080/02640414.2012.667877"
STONE_2025 = "https://doi.org/10.1080/24748668.2025.2481697"
LIU_2025 = "https://doi.org/10.3389/fpsyg.2025.1510928"
STATMUSE = "https://www.statmuse.com/fc"
ZHAO_LIU_2022 = "https://doi.org/10.3389/fpsyg.2022.907336"


@dataclass(frozen=True)
class Ref:
    metric: str
    values: tuple[float, ...]
    """Per-match values (both teams), one per cited season; rates as fractions."""
    seasons: str
    url: str
    quote: str
    verified: bool
    kind: str = "count"
    """count (band +/-10%) or rate (band +/-3 points)."""

    def band(self) -> tuple[float, float]:
        lo, hi = min(self.values), max(self.values)
        if self.kind == "rate":
            return lo - 0.03, hi + 0.03
        return lo * 0.9, hi * 1.1


GATED: tuple[Ref, ...] = (
    Ref("goals", (2.85, 3.28, 2.99), "EPL 2022-23, 2023-24, 2024-25 (to 24 Mar 2025)", PL_TRENDS,
        "Goals/90 2.82 2.85 3.28 2.99", True),
    Ref("shots", (25.3, 27.6, 26.1), "EPL 2022-23, 2023-24, 2024-25 (to 24 Mar 2025)", PL_TRENDS,
        "Shots/90 25.7 25.3 27.6 26.1", True),
    Ref("pass_completion", (0.813, 0.829, 0.837), "EPL 2022-23, 2023-24, 2024-25", PL_PASS,
        "2022/23 905.7 81.3 / 2023/24 941.0 82.9 / 2024/25 893.5 83.7", True, "rate"),
    Ref("yellows", (4.2,), "EPL 2023-24", AN_CARDS,
        "the highest average for yellow cards per game over a full season was the 4.2 in 2023-24", True),
    Ref("effective_play_min", (54.82, 58.18, 56.98), "EPL 2022-23, 2023-24, 2024-25", AN_BIP,
        "54 minutes and 49 seconds in 2022-23, to 58 minutes and 11 seconds in 2023-24", True),
    Ref("corners", (3890 / 380,), "EPL 2024-25", AN_BIP,
        "the same number of corners in the Premier League as last season (3,890)", True),
    Ref("home_win_share", (0.484, 0.390), "EPL 2022-23; 2024-25 (part season)", AN_HOME,
        "as recently as 2022-23 (48.4%) ... just 39.0% of games in 2024-25 have been won by the hosts",
        True, "rate"),
)  # fmt: skip
"""The eight G3 checks of the approved plan, fouls excepted (no first-hand source; reported below)."""

REPORTED: tuple[Ref, ...] = (
    Ref("fouls", (21.5, 22.1), "EPL 2023-24", LIU_2025,
        "during the 2023-2024 season, the England Premier League (EPL) averaged 21.5 fouls per match", False),
    Ref("penalties", (0.26, 0.28, 0.22), "EPL 2022-23, 2023-24, 2024-25 (part)", PL_TRENDS,
        "Penalties/90 0.27 0.26 0.28 0.22", True),
    Ref("passes", (905.7, 941.0, 893.5), "EPL 2022-23, 2023-24, 2024-25", PL_PASS,
        "The average in the 2023/24 season came close to matching that (941.0)", True),
    Ref("throw_ins", (39.0,), "top-5 leagues 2022-23 (StatsBomb)", STONE_2025,
        "On average, 39 throw-ins were taken per match.", False),
    Ref("goal_kicks", (17.0,), "Bundesliga sample", SIEGLE_LAMES_2012,
        "Goal kicks (17), corner kicks (10), substitutions (4), and kick offs (3) occurred less often.",
        False),
    Ref("free_kicks_incl_offside", (33.0,), "Bundesliga sample", SIEGLE_LAMES_2012,
        "Throw-ins (40) and free kicks (33) were most frequent.", False),
    Ref("interruptions", (108.0,), "Bundesliga sample", SIEGLE_LAMES_2012,
        "there is an average of 108 interruptions per match", False),
    Ref("offsides", (3.84,), "EPL 2023-24 (sum of team tables)", STATMUSE,
        "computed by the research pass from StatMuse team offsides per game", False),
    Ref("shots_on_target", (9.88, 9.10), "EPL 2023-24, 2024-25 (sum of team tables)", STATMUSE,
        "computed by the research pass from StatMuse team shots on target per game", False),
    Ref("reds", (0.08, 0.155), "EPL 2022-23, 2023-24", STATMUSE,
        "2022-23: 30 in 380 matches; 2023-24: computed from StatMuse team tables", False),
    Ref("added_time_min", (9.73,), "EPL 2024-25 (match length minus 90)", AN_BIP,
        "Games are lasting on average 51 seconds longer than last season (99 mins, 44 secs)", True),
)  # fmt: skip

RESTART_SECONDS: dict[str, tuple[float, str]] = {
    # Mean dead time per restart, EPL 2024-25 (AN_BIP, verified): the engine's medians are set from these.
    "corner": (33.6, "up to 36.9 seconds from 33.6 seconds last season"),
    "goal_kick": (28.3, "Goal-kicks are also taking two seconds longer (30.3 seconds, up from 28.3 seconds)"),
    "throw_in": (15.6, "up to 17.7 seconds this season from 15.6 seconds in 2024-25"),
}

NOT_SOURCED = (
    "Shot speed in matches: no league-wide median or range found. The Bundesliga Match Facts article "
    "(AWS machine-learning blog, 'Bundesliga Match Facts Shot Speed') counts 'fast shots (>100 km/h)' "
    "and a 137 km/h record only. The EURO 2024 velocity paper (doi 10.3390/data11020039) returned HTTP 403.",
    "Pass speed in matches: no source found.",
    "Added time per half: no first-half / second-half split found.",
)
