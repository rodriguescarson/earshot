"""The events-first match simulator.

One action per step, in plain Python. The ball carrier picks an action with a softmax over
utilities that the team's tactical settings move; outcomes are drawn from simple models of pass
success, duels, shots and fouls. Stoppages are explicit events with per-type dead time (eng
review E-2). Positions are in the broadcast frame on every emitted event; internally each team
reasons in its own attacking frame (own goal at x=0).

Everything random comes from ``earshot.rng`` streams keyed by the match seed, so a seed always
produces byte-identical events (gate G1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from earshot.contract.claim import CauseLabel
from earshot.contract.events import (
    Action,
    Card,
    CardColor,
    CardReason,
    Event,
    Injury,
    Kickoff,
    PeriodEnd,
    Restart,
    StoppageKind,
    StoppageStart,
    Substitution,
    VarCheck,
)
from earshot.contract.match import MatchInfo, TeamSheet
from earshot.contract.truth import TruthChange
from earshot.pitch import PITCH_LENGTH as PL
from earshot.pitch import PITCH_WIDTH as PW
from earshot.pitch import attacks_right
from earshot.rng import stream
from earshot.sim import tactics
from earshot.sim.formations import FORMATIONS, TEN_MAN, TEN_MAN_FOR, Slot, assign
from earshot.sim.stoppages import DEAD_TIME, EXTRA, FREE_KICK_DANGEROUS
from earshot.world.clubs import Club, Fixture, Player, World, fixture_for_seed, world

K = StoppageKind
ENGINE_VERSION = "1.0.0"
PERIOD_MS = 45 * 60_000
GOAL_LO, GOAL_HI = 30.34, 37.66
BOX_X = 88.5
BOX_HALF_W = 20.16

# Global calibration settings (tuned on the development seeds; see EVALUATION.md, G3).
SHOT_BIAS = 4.2
PASS_BASE = 3.35
HOME_EDGE = 0.04

SET_PIECE_NEAR = {
    "pass": (9.15, 13.0),  # kick-off and dropped balls
    "throw_in": (2.0, 6.0),
    "goal_kick": (15.0, 30.0),
    "corner_crossed": (9.15, 12.0),
    "corner_short": (9.15, 12.0),
    "freekick_short": (9.15, 14.0),
    "freekick_crossed": (9.15, 12.0),
    "shot_freekick": (9.15, 10.0),
    "shot_penalty": (11.0, 11.0),
}


def _sig(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _other(side: str) -> str:
    return "away" if side == "home" else "home"


def in_box(ax: float, ay: float) -> bool:
    return ax > BOX_X and abs(ay - PW / 2) < BOX_HALF_W


def xg(ax: float, ay: float, header: bool = False, pressured: bool = False) -> float:
    """Chance quality from distance and goal-mouth angle, in the shooter's attacking frame."""
    dx = max(0.5, PL - ax)
    dy = abs(PW / 2 - ay)
    d = math.hypot(dx, dy)
    a = math.atan2(7.32 * dx, dx * dx + dy * dy - (7.32 / 2) ** 2)
    if a < 0:
        a += math.pi
    z = -1.8 + 1.4 * a - 0.085 * d - (0.75 if header else 0.0) - (0.35 if pressured else 0.0)
    return _sig(z)


@dataclass
class Team:
    side: str
    club: Club
    formation: str
    slots: dict[str, Slot]
    bench: list[Player]
    base: dict[str, float]
    planned: list[tactics.PlannedChange]
    gone: set[str] = field(default_factory=set)
    yellow: set[str] = field(default_factory=set)
    on_minute: dict[str, float] = field(default_factory=dict)
    changes: list[tactics.ActiveChange] = field(default_factory=list)
    subs_used: int = 0
    windows_used: int = 0
    chase_checked: bool = False
    protect_checked: bool = False
    goals: int = 0

    @property
    def team_id(self) -> str:
        return self.club.team_id


@dataclass
class MatchResult:
    info: MatchInfo
    events: list[Event]
    truth: list[TruthChange]
    home_goals: int
    away_goals: int


class MatchSim:
    def __init__(self, seed: int, fixture: Fixture | None = None, w: World | None = None) -> None:
        self.seed = seed
        self.w = w or world()
        self.fx = fixture or fixture_for_seed(seed)
        self.match_id = f"m{seed:05d}"
        self.rng = stream(seed, "play")
        self.rdead = stream(seed, "dead")
        self.players = self.w.players
        self.teams = {
            "home": self._team("home", self.w.club(self.fx.home)),
            "away": self._team("away", self.w.club(self.fx.away)),
        }
        self.kickoff_side = "home" if stream(seed, "toss").random() < 0.5 else "away"
        self.events: list[Event] = []
        self.truth: list[TruthChange] = []
        self.period: int = 1
        self.t = 0
        self.offset = 0
        self.extra = 0
        self.end_ms: int | None = None
        self.poss = self.kickoff_side
        self.carrier = ""
        self.bx, self.by = PL / 2, PW / 2
        self._s: dict[str, dict[str, float]] = {}
        self.half_time_ms = 0
        ref = stream(seed, "referee")
        self.referee = self.w.referees[int(ref.integers(len(self.w.referees)))].full

    # ----- setup -------------------------------------------------------------------------------

    def _team(self, side: str, club: Club) -> Team:
        r = stream(self.seed, "lineup", side)
        squad = list(club.players)
        # Small per-match form noise so the same XI does not play identically every week.
        form = {p.player_id: float(r.normal(0, 0.04)) for p in squad}
        slots = assign(FORMATIONS[club.formation], squad, key=lambda p: p.rating + form[p.player_id])
        rest = sorted((p for p in squad if p.player_id not in slots), key=lambda p: (-p.rating, p.player_id))
        gk = [p for p in rest if p.position == "GK"][:1]
        bench = gk + [p for p in rest if p.position != "GK"][:8]
        base = {k: _clamp(v + float(r.normal(0, 0.03)), 0.08, 0.92) for k, v in club.settings.items()}
        planned = tactics.plan(stream(self.seed, "truth", side), base)
        team = Team(side, club, club.formation, slots, bench, base, planned)
        team.on_minute = {pid: 0.0 for pid in slots}
        return team

    def info(self) -> MatchInfo:
        def sheet(side: str) -> TeamSheet:
            t = self.teams[side]
            starting = [pid for pid, m in t.on_minute.items() if m == 0.0][:11]
            return TeamSheet(
                team_id=t.team_id,
                formation=t.club.formation,
                starting=starting,
                bench=[p.player_id for p in self._initial_bench[side]],
            )

        return MatchInfo(
            match_id=self.match_id,
            seed=self.seed,
            engine_version=ENGINE_VERSION,
            round=self.fx.round,
            home=sheet("home"),
            away=sheet("away"),
            referee=self.referee,
            kickoff_team_id=self.teams[self.kickoff_side].team_id,
            half_time_ms=self.half_time_ms,
        )

    # ----- frames and time ---------------------------------------------------------------------

    def right(self, side: str) -> bool:
        return attacks_right(side == "home", self.period)

    def to_b(self, side: str, ax: float, ay: float) -> tuple[float, float]:
        x, y = (ax, ay) if self.right(side) else (PL - ax, PW - ay)
        return round(_clamp(x, 0.0, PL), 2), round(_clamp(y, 0.0, PW), 2)

    def to_a(self, side: str, x: float, y: float) -> tuple[float, float]:
        return (x, y) if self.right(side) else (PL - x, PW - y)

    def minute(self) -> float:
        return (self.period - 1) * 45 + self.t / 60_000

    def wall(self) -> int:
        return self.offset + self.t

    def settings(self, side: str) -> dict[str, float]:
        s = self._s.get(side)
        if s is None:
            t = self.teams[side]
            s = tactics.effective(t.base, t.changes, self.wall(), self.minute())
            self._s[side] = s
        return s

    def emit(self, cls, side: str | None = None, player: str | None = None, **kw) -> None:
        team_id = self.teams[side].team_id if side else None
        self.events.append(
            cls(
                event_id=f"{self.match_id}-{len(self.events):05d}",
                match_id=self.match_id,
                period_id=self.period,
                clock_ms=self.t,
                wall_ms=self.wall(),
                team_id=team_id,
                player_id=player,
                **kw,
            )
        )

    def set_piece_near(self, type_: str) -> float:
        """Distance to the nearest opponent at a restart, where the laws keep opponents back."""
        lo, hi = SET_PIECE_NEAR.get(type_, (1.0, 6.0))
        return float(self.rng.uniform(lo, hi))

    def act(
        self,
        side: str,
        player: str,
        type_: str,
        result: str,
        a0,
        a1,
        *,
        flight_ms: int | None = None,
        near: float | None = None,
        bodypart: str = "foot",
    ) -> None:
        if near is None:
            near = self.set_piece_near(type_)
        x0, y0 = self.to_b(side, *a0)
        x1, y1 = self.to_b(side, *a1)
        self.emit(
            Action,
            side,
            player,
            type=type_,
            result=result,
            bodypart=bodypart,
            start_x=x0,
            start_y=y0,
            end_x=x1,
            end_y=y1,
            flight_ms=flight_ms,
            nearest_opp_m=None if near is None else round(near, 2),
        )

    def put_ball(self, side: str, ax: float, ay: float) -> None:
        self.bx, self.by = self.to_b(side, ax, ay)

    # ----- positions ---------------------------------------------------------------------------

    def positions(self, side: str, in_poss: bool) -> dict[str, tuple[float, float]]:
        """Where each on-pitch player stands now, in ``side``'s attacking frame."""
        team = self.teams[side]
        s = self.settings(side)
        bax, bay = self.to_a(side, self.bx, self.by)
        if in_poss:
            # Between our back line and the opponents' last line (attackers hold the offside line).
            front = PL - self.defensive_line(_other(side), PL - bax) - 1.0
            back = min(_clamp(16 + 24 * s["line_height"] + 0.32 * (bax - 40), 10, 58), front - 25)
            wscale = 0.78 + 0.45 * s["width"]
        else:
            back = self.defensive_line(side, bax)
            front = back + 28 + 18 * s["press_height"]
            wscale = 0.70
        out = {}
        for pid, slot in team.slots.items():
            if slot.role == "GK":
                x, y = _clamp(4 + 0.07 * bax, 2, 16), PW / 2 + 0.12 * (bay - PW / 2)
            else:
                frac = _clamp((slot.x - 21) / 47, -0.1, 1.05)
                x = back + frac * (front - back)
                y = PW / 2 + (slot.y - PW / 2) * wscale + 0.22 * (bay - PW / 2)
            out[pid] = (_clamp(x, 1.0, PL - 1.0), _clamp(y, 1.0, PW - 1.0))
        return out

    def defensive_line(self, side: str, bax: float) -> float:
        """The defending ``side``'s back line (its own frame) when the ball is at ``bax`` (same frame)."""
        return _clamp(14 + 26 * self.settings(side)["line_height"] + 0.30 * (bax - 52), 8, 52)

    def nearest(
        self,
        side: str,
        ax: float,
        ay: float,
        in_poss: bool,
        exclude: str = "",
        roles: tuple[str, ...] = ("DF", "MF", "FW", "GK"),
    ) -> str:
        """Nearest ``side`` player to a point given in ``side``'s own frame."""
        pos = self.positions(side, in_poss)
        team = self.teams[side]
        best, bd = "", 1e9
        for pid, (x, y) in pos.items():
            if pid == exclude or team.slots[pid].role not in roles:
                continue
            d = (x - ax) ** 2 + (y - ay) ** 2
            if d < bd:
                best, bd = pid, d
        return best or next(p for p in pos if p != exclude)

    def gk(self, side: str) -> str:
        team = self.teams[side]
        return next((pid for pid, s in team.slots.items() if s.role == "GK"), next(iter(team.slots)))

    # ----- the match ---------------------------------------------------------------------------

    def run(self) -> MatchResult:
        self._initial_bench = {s: list(t.bench) for s, t in self.teams.items()}
        self.kickoff(self.kickoff_side)
        self.play()
        self.half_time()
        self.kickoff(_other(self.kickoff_side))
        self.play()
        self.full_time()
        h, a = self.teams["home"].goals, self.teams["away"].goals
        return MatchResult(self.info(), self.events, self.truth, h, a)

    def play(self) -> None:
        while True:
            self._s = {}
            self.tick_tactics()
            if self.end_ms is None and self.t >= PERIOD_MS:
                added = math.ceil(self.extra * 0.6 / 60_000)
                added = int(_clamp(added, 1 if self.period == 1 else 3, 10))
                self.end_ms = PERIOD_MS + added * 60_000 + int(self.rng.integers(0, 40_000))
            if self.end_ms is not None and self.t >= self.end_ms:
                return
            self.step()

    def half_time(self) -> None:
        second = _other(self.kickoff_side)
        self.emit(
            StoppageStart,
            kind=K.HALF_TIME,
            restart_team_id=self.teams[second].team_id,
            expected_dead_ms=DEAD_TIME[K.HALF_TIME].median_ms,
        )
        self.emit(PeriodEnd)
        end_wall = self.wall()
        self.half_time_ms = DEAD_TIME[K.HALF_TIME].draw(self.rdead)
        self.period, self.t, self.offset, self.extra, self.end_ms = (
            2,
            0,
            end_wall + self.half_time_ms,
            0,
            None,
        )
        self._s = {}
        for side in ("home", "away"):
            if self.rng.random() < 0.22:
                self.substitute(side, 1, window=False)
        self.emit(Restart, kind=K.HALF_TIME)

    def full_time(self) -> None:
        self.emit(StoppageStart, kind=K.FULL_TIME, restart_team_id=None, expected_dead_ms=0)
        self.emit(PeriodEnd)

    def kickoff(self, side: str) -> None:
        self.poss = side
        self.put_ball(side, PL / 2, PW / 2)
        taker = self.nearest(side, PL / 2, PW / 2, True, roles=("FW", "MF"))
        self.emit(Kickoff, side, taker)
        self.carrier = taker
        self.pass_(side, PL / 2, PW / 2, None, False, mode="back")

    # ----- tactics and truth -------------------------------------------------------------------

    def start_change(
        self, side: str, label: CauseLabel, deltas: dict[str, float], ramp_ms: int, delay_ms: int = 0
    ) -> None:
        team = self.teams[side]
        start = self.wall() + delay_ms
        team.changes.append(tactics.ActiveChange(label, start, ramp_ms, deltas))
        primary = next(iter(tactics.LABEL_MOVES[label]))
        self.truth.append(
            TruthChange(
                match_id=self.match_id,
                team_id=team.team_id,
                label=label,
                wall_ms=start,
                ramp_ms=ramp_ms,
                setting=primary,
                delta=deltas.get(primary, 0.0),
                deltas=deltas,
            )
        )
        self._s = {}

    def current_settings(self, side: str) -> dict[str, float]:
        t = self.teams[side]
        s = dict(t.base)
        for c in t.changes:
            for k, d in c.deltas.items():
                s[k] += d
        return s

    def fitted(self, side: str, label: CauseLabel, magnitude: float) -> dict[str, float]:
        """Deltas for ``label`` scaled down just enough to stay inside the setting bounds."""
        cur = self.current_settings(side)
        out = {}
        for k, v in tactics.LABEL_MOVES[label].items():
            d = v * magnitude
            out[k] = round(_clamp(cur[k] + d, tactics.LO, tactics.HI) - cur[k], 4)
        return out

    def tick_tactics(self) -> None:
        m = self.minute()
        for side, team in self.teams.items():
            while team.planned and team.planned[0].minute <= m:
                pc = team.planned.pop(0)
                self.start_change(side, pc.label, tactics.deltas_for(pc.label, pc.magnitude), pc.ramp_ms)
            if m < 65 or self.period != 2:
                continue
            diff = team.goals - self.teams[_other(side)].goals
            if not team.chase_checked and diff in (-1, -2):
                team.chase_checked = True
                if self.rng.random() < 0.7:
                    mag = float(self.rng.uniform(0.25, 0.35))
                    self.start_change(
                        side,
                        CauseLabel.CHASING_GAME,
                        self.fitted(side, CauseLabel.CHASING_GAME, mag),
                        int(self.rng.integers(60_000, 120_001)),
                    )
            if not team.protect_checked and m >= 72 and diff == 1:
                team.protect_checked = True
                if self.rng.random() < 0.6:
                    mag = float(self.rng.uniform(0.25, 0.35))
                    self.start_change(
                        side,
                        CauseLabel.PROTECTING_LEAD,
                        self.fitted(side, CauseLabel.PROTECTING_LEAD, mag),
                        int(self.rng.integers(60_000, 120_001)),
                    )

    # ----- one step ----------------------------------------------------------------------------

    def step(self) -> None:
        side, opp = self.poss, _other(self.poss)
        s, so = self.settings(side), self.settings(opp)
        ax, ay = self.to_a(side, self.bx, self.by)
        carrier = self.carrier
        # Time on the ball before the next action: tempo sets it.
        self.t += int(
            max(250, (1700 - 1150 * s["tempo"]) * math.exp(0.5 * float(self.rng.standard_normal())))
        )
        if self.rng.random() < 0.0004:
            self.injury_stoppage(side, ax, ay)
            return
        intensity = self.press_intensity(so, ax)
        near = _clamp((7.5 - 6.0 * intensity) * math.exp(0.45 * float(self.rng.standard_normal())), 0.3, 25.0)
        pressured = near < 3.0
        if self.challenge(side, opp, ax, ay, near, pressured):
            return
        choice = self.choose(side, s, ax, ay, pressured)
        if choice == "short":
            self.pass_(side, ax, ay, near, pressured, mode="short")
        elif choice == "long":
            self.pass_(side, ax, ay, near, pressured, mode="long")
        elif choice == "cross":
            self.cross(side, ax, ay, near, pressured)
        elif choice == "dribble":
            self.dribble(side, ax, ay, near)
        elif choice == "take_on":
            self.take_on(side, ax, ay, near)
        elif choice == "shot":
            self.shot(side, ax, ay, near, pressured)
        else:
            self.clearance(side, carrier, ax, ay, near, retain=0.35)

    def press_intensity(self, so: dict[str, float], ax: float) -> float:
        dist_from_def_goal = PL - ax
        press_line = 30 + 50 * so["press_height"]
        i = 0.12 + 0.72 * _sig((press_line - dist_from_def_goal) / 7.0)
        if dist_from_def_goal < 30:
            i = max(i, 0.62 + 0.25 * (30 - dist_from_def_goal) / 30)
        return i

    def choose(self, side: str, s: dict[str, float], ax: float, ay: float, pressured: bool) -> str:
        role = self.teams[side].slots[self.carrier].role
        rating = self.players[self.carrier].rating
        opts: dict[str, float] = {
            "short": 1.7 + 0.9 * (1 - s["directness"]) - (0.5 if pressured else 0.0),
            "long": -0.6 + 2.0 * s["directness"] + (0.5 if ax < 50 else 0.0) - (1.6 if ax > 78 else 0.0),
        }
        if role != "GK":
            opts["dribble"] = 0.7 + (-0.5 if pressured else 0.5) + (0.3 if 30 < ax < 85 else 0.0)
            opts["take_on"] = (
                -1.7 + (0.9 if pressured else 0.0) + (0.6 if ax > 70 else 0.0) + 1.5 * (rating - 0.5)
            )
            if ax > 70 and (ay < 20 or ay > PW - 20):
                opts["cross"] = 0.6 + 1.3 * s["width"] + (0.8 if ax > 88 else 0.0)
            d_goal = math.hypot(PL - ax, PW / 2 - ay)
            if d_goal < 32:
                opts["shot"] = SHOT_BIAS + 1.39 * math.log(xg(ax, ay, False, pressured))
            if ax < 26 and pressured:
                opts["clearance"] = 0.5 + 0.6 * s["directness"]
        names = list(opts)
        w = np.exp(np.array([opts[n] for n in names]) - max(opts.values()))
        return names[int(self.rng.choice(len(names), p=w / w.sum()))]

    # ----- duels and fouls ---------------------------------------------------------------------

    def challenge(self, side: str, opp: str, ax: float, ay: float, near: float, pressured: bool) -> bool:
        p_try = (0.20 if pressured else 0.025) * (1.5 if ax > 75 else 1.0)
        if self.rng.random() >= p_try:
            return False
        dax, day = PL - ax, PW - ay
        defender = self.nearest(opp, dax, day, False, roles=("DF", "MF", "FW"))
        box = in_box(ax, ay)
        p_foul = 0.16 * (0.07 if box else 1.0) * (1.2 if self.minute() > 70 else 1.0)
        if defender in self.teams[opp].yellow:
            p_foul *= 0.6
        duel = _clamp(
            0.45 + 0.8 * (self.players[defender].rating - self.players[self.carrier].rating), 0.15, 0.8
        )
        u = self.rng.random()
        if u < p_foul:
            self.foul(opp, defender, side, ax, ay, box)
            return True
        if u < p_foul + (1 - p_foul) * duel:
            self.act(opp, defender, "tackle", "success", (dax, day), (dax, day), near=near)
            if min(ay, PW - ay) < 10 and self.rng.random() < 0.4:
                oy = 0.0 if ay < PW / 2 else PW
                to = side if self.rng.random() < 0.6 else opp
                self.t += int(self.rng.integers(300, 900))
                self.poss = to
                spot = (ax, PW - oy) if to == side else (PL - ax, oy)
                self.dead(K.THROW_IN, to, spot[0], spot[1] if to == side else PW - oy)
                return True
            self.poss, self.carrier = opp, defender
            return True
        self.act(opp, defender, "tackle", "fail", (dax, day), (dax, day), near=near)
        return False

    def foul(self, opp: str, fouler: str, side: str, ax: float, ay: float, box: bool) -> None:
        """``fouler`` (team ``opp``) fouls the ball carrier of ``side`` at (ax, ay) in side's frame."""
        team = self.teams[opp]
        r = self.rng.random()
        color, reason = None, None
        if r < 0.002:
            color = CardColor.RED
            reason = CardReason.DENYING_GOAL_CHANCE if (box or ax > 80) else CardReason.FOUL
        elif r < 0.002 + 0.165 * (1.3 if self.minute() > 60 else 1.0) * (
            0.45 if fouler in team.yellow else 1.0
        ):
            if fouler in team.yellow:
                color, reason = CardColor.RED, CardReason.SECOND_YELLOW
            else:
                color, reason = CardColor.YELLOW, CardReason.FOUL
        result = {None: "fail", CardColor.YELLOW: "yellow_card", CardColor.RED: "red_card"}[color]
        self.act(opp, fouler, "foul", result, (PL - ax, PW - ay), (PL - ax, PW - ay), near=0.5)
        card = (opp, fouler, color, reason) if color else None
        injured = self.carrier if self.rng.random() < 0.05 else None
        self.poss = side
        if box:
            self.dead(K.PENALTY, side, ax, ay, card=card, injured=injured)
        else:
            self.dead(K.FREE_KICK, side, ax, ay, card=card, injured=injured)

    # ----- on-ball actions ---------------------------------------------------------------------

    def pass_(
        self,
        side: str,
        ax: float,
        ay: float,
        near: float | None,
        pressured: bool,
        *,
        mode: str,
        type_: str = "pass",
        bonus: float = 0.0,
    ) -> None:
        opp = _other(side)
        s, so = self.settings(side), self.settings(opp)
        team = self.teams[side]
        pos = self.positions(side, True)
        passer = self.carrier
        best: list[tuple[str, float]] = []
        for pid, (px, py) in pos.items():
            if pid == passer:
                continue
            d = math.hypot(px - ax, py - ay)
            fwd = px - ax
            if team.slots[pid].role == "GK" and (ax > 40 or mode != "short"):
                continue
            if mode == "back":
                w = math.exp(-(((d - 12) / 6) ** 2)) * (1.0 if fwd < 0 else 0.05)
            elif mode == "short":
                if d < 4 or d > 32:
                    continue
                w = math.exp(-(((d - 13) / 7) ** 2)) * math.exp((0.02 + 0.05 * s["directness"]) * fwd)
            else:
                if d < 18:
                    continue
                w = math.exp(-(((d - 34) / 12) ** 2)) * math.exp((0.04 + 0.05 * s["directness"]) * fwd)
            best.append((pid, w))
        if not best:
            best = [(pid, 1.0) for pid in pos if pid != passer]
        weights = np.array([w for _, w in best])
        receiver = best[int(self.rng.choice(len(best), p=weights / weights.sum()))][0]
        rx, ry = pos[receiver]
        long_ball = mode == "long"
        run = float(self.rng.uniform(2, 9)) if long_ball else 0.0
        tx = _clamp(rx + run + float(self.rng.normal(0, 1.5)), 0.5, PL - 0.5)
        ty = _clamp(ry + float(self.rng.normal(0, 1.5)), 0.3, PW - 0.3)
        dist = math.hypot(tx - ax, ty - ay)
        fwd = tx - ax
        rating = self.players[passer].rating
        opp_strength = self.teams[opp].club.strength
        edge = HOME_EDGE if side == "home" else -HOME_EDGE
        logit = (
            PASS_BASE
            + edge
            - 0.045 * dist
            - 0.012 * max(0.0, fwd)
            - (1.0 if pressured else 0.0)
            - (0.5 if long_ball else 0.0)
            - (0.7 if in_box(tx, ty) else 0.0)
            - (0.35 if tx > 75 else 0.0)
            + 1.6 * (rating - 0.5)
            - 1.2 * (opp_strength - 0.5)
            + bonus
        )
        if mode == "back":
            logit += 1.5
        p_ok = _sig(logit)
        speed = float(self.rng.normal(19, 3) if long_ball else self.rng.normal(13, 2))
        flight = int(max(150, dist / max(6.0, speed) * 1000))
        # Offside: a forward ball to a receiver beyond the opponents' line.
        opp_line_in_ours = PL - self.defensive_line(opp, PL - ax)
        no_offside = type_ in ("throw_in", "goal_kick", "corner_short")
        if not no_offside and fwd > 5 and tx > opp_line_in_ours + 1 and tx > PL / 2:
            p_off = (0.03 + 0.07 * so["line_height"]) if long_ball else 0.006
            if self.rng.random() < p_off:
                self.act(side, passer, type_, "offside", (ax, ay), (tx, ty), flight_ms=flight, near=near)
                self.t += flight
                self.poss = opp
                self.dead(K.OFFSIDE, opp, PL - tx, PW - ty)
                return
        if self.rng.random() < p_ok:
            self.act(side, passer, type_, "success", (ax, ay), (tx, ty), flight_ms=flight, near=near)
            self.t += flight
            self.carrier = receiver
            self.put_ball(side, tx, ty)
            return
        self.failed_ball(side, passer, type_, ax, ay, tx, ty, flight, near)

    def failed_ball(
        self,
        side: str,
        passer: str,
        type_: str,
        ax: float,
        ay: float,
        tx: float,
        ty: float,
        flight: int,
        near: float | None,
    ) -> None:
        """A pass or cross that does not reach a team-mate: out of play, or intercepted."""
        opp = _other(side)
        edge = min(ty, PW - ty)
        if self.rng.random() < (0.65 if edge < 4 else 0.50 if edge < 12 else 0.14):
            oy = 0.0 if ty < PW / 2 else PW
            ox = _clamp(tx, 1.0, PL - 1.0)
            self.act(side, passer, type_, "fail", (ax, ay), (ox, oy), flight_ms=flight, near=near)
            self.t += flight
            self.poss = opp
            self.dead(K.THROW_IN, opp, PL - ox, PW - oy)
            return
        if tx > PL - 2.5 and self.rng.random() < 0.6:
            self.act(side, passer, type_, "fail", (ax, ay), (PL, ty), flight_ms=flight, near=near)
            self.t += flight
            self.poss = opp
            self.dead(K.GOAL_KICK, opp, 5.5, PW / 2 + (9.16 if ty < PW / 2 else -9.16))
            return
        f = float(self.rng.uniform(0.35, 1.0))
        ix, iy = ax + f * (tx - ax), ay + f * (ty - ay)
        self.act(
            side, passer, type_, "fail", (ax, ay), (ix, iy), flight_ms=max(100, int(flight * f)), near=near
        )
        self.t += int(flight * f)
        interceptor = self.nearest(opp, PL - ix, PW - iy, False)
        self.t += int(self.rng.integers(150, 600))
        self.act(
            opp,
            interceptor,
            "interception",
            "success",
            (PL - ix, PW - iy),
            (PL - ix, PW - iy),
            near=float(self.rng.uniform(0.5, 4.0)),
        )
        self.poss, self.carrier = opp, interceptor
        self.put_ball(opp, PL - ix, PW - iy)

    def dribble(self, side: str, ax: float, ay: float, near: float) -> None:
        if ax > 80:
            # Near goal, carries go across the box rather than towards the byline.
            theta = math.atan2(PW / 2 - ay, PL - ax) + float(self.rng.normal(0, 0.9))
            length = float(self.rng.uniform(2, 6))
        else:
            theta = float(self.rng.normal(0, 0.6)) * (1 if self.rng.random() < 0.5 else -1)
            length = float(self.rng.uniform(3, 12))
        nx = _clamp(ax + length * math.cos(theta), 1.0, PL - 4.0)
        ny = _clamp(ay + length * math.sin(theta), 1.0, PW - 1.0)
        dur = int(math.hypot(nx - ax, ny - ay) / float(self.rng.uniform(4.5, 6.5)) * 1000)
        self.act(side, self.carrier, "dribble", "success", (ax, ay), (nx, ny), near=near)
        self.t += max(200, dur)
        self.put_ball(side, nx, ny)

    def take_on(self, side: str, ax: float, ay: float, near: float) -> None:
        opp = _other(side)
        defender = self.nearest(opp, PL - ax, PW - ay, False, roles=("DF", "MF", "FW"))
        p = _clamp(0.5 + 0.8 * (self.players[self.carrier].rating - self.players[defender].rating), 0.2, 0.8)
        if self.rng.random() < p:
            nx, ny = (
                _clamp(ax + float(self.rng.uniform(3, 6)), 1, PL - 1),
                _clamp(ay + float(self.rng.normal(0, 2)), 1, PW - 1),
            )
            self.act(side, self.carrier, "take_on", "success", (ax, ay), (nx, ny), near=near)
            self.t += int(self.rng.integers(500, 1200))
            self.put_ball(side, nx, ny)
            return
        if self.rng.random() < 0.16:
            self.foul(opp, defender, side, ax, ay, in_box(ax, ay))
            return
        self.act(side, self.carrier, "take_on", "fail", (ax, ay), (ax, ay), near=near)
        self.t += int(self.rng.integers(300, 800))
        self.act(opp, defender, "tackle", "success", (PL - ax, PW - ay), (PL - ax, PW - ay), near=0.6)
        self.poss, self.carrier = opp, defender

    def cross(
        self, side: str, ax: float, ay: float, near: float, pressured: bool, *, type_: str = "cross"
    ) -> None:
        opp = _other(side)
        tx = float(self.rng.uniform(91, 101.5))
        ty = _clamp(float(self.rng.normal(PW / 2, 6)), GOAL_LO - 9, GOAL_HI + 9)
        dist = math.hypot(tx - ax, ty - ay)
        flight = int(max(300, dist / max(10.0, float(self.rng.normal(20, 3))) * 1000))
        crosser = self.carrier
        p_ok = _clamp(
            0.26 + 0.25 * (self.players[crosser].rating - 0.5) - (0.06 if pressured else 0.0), 0.08, 0.5
        )
        if self.rng.random() < p_ok:
            receiver = self.nearest(side, tx, ty, True, exclude=crosser, roles=("FW", "MF", "DF"))
            self.act(side, crosser, type_, "success", (ax, ay), (tx, ty), flight_ms=flight, near=near)
            self.t += flight
            self.carrier = receiver
            self.put_ball(side, tx, ty)
            if self.rng.random() < 0.55:
                self.shot(side, tx, ty, float(self.rng.uniform(0.5, 2.5)), True, header=True)
            return
        u = self.rng.random()
        if tx > 96 and u < 0.22:
            g = self.gk(opp)
            self.act(side, crosser, type_, "fail", (ax, ay), (tx, ty), flight_ms=flight, near=near)
            self.t += flight
            self.act(
                opp,
                g,
                "keeper_claim",
                "success",
                (PL - tx, PW - ty),
                (PL - tx, PW - ty),
                near=1.0,
                bodypart="other",
            )
            self.poss, self.carrier = opp, g
            self.put_ball(opp, PL - tx, PW - ty)
            return
        if u < 0.80:
            self.act(side, crosser, type_, "fail", (ax, ay), (tx, ty), flight_ms=flight, near=near)
            self.t += flight
            dfd = self.nearest(opp, PL - tx, PW - ty, False, roles=("DF", "MF"))
            self.poss, self.carrier = opp, dfd
            self.put_ball(opp, PL - tx, PW - ty)
            if self.rng.random() < 0.012:
                self.own_goal(opp, dfd, PL - tx, PW - ty)
                return
            self.clearance(
                opp, dfd, PL - tx, PW - ty, float(self.rng.uniform(0.5, 2.0)), retain=0.4, corner=0.30
            )
            return
        # Over everyone and out behind the goal.
        self.act(side, crosser, type_, "fail", (ax, ay), (PL, ty), flight_ms=flight, near=near)
        self.t += flight
        self.poss = opp
        self.dead(K.GOAL_KICK, opp, 5.5, PW / 2)

    def clearance(
        self, side: str, player: str, ax: float, ay: float, near: float, *, retain: float, corner: float = 0.0
    ) -> None:
        opp = _other(side)
        if corner and self.rng.random() < corner:
            # Cleared behind its own goal line: a corner to the attackers.
            ey = 0.0 if ay < PW / 2 else PW
            self.act(side, player, "clearance", "fail", (ax, ay), (0.0, ey), flight_ms=400, near=near)
            self.t += 400
            self.poss = opp
            self.dead(K.CORNER, opp, PL, PW - ey)
            return
        length = float(self.rng.uniform(25, 55))
        theta = float(self.rng.normal(0, 0.5))
        ex = _clamp(ax + length * math.cos(theta), 1, PL - 1)
        ey_raw = ay + length * math.sin(theta)
        flight = int(length / max(12.0, float(self.rng.normal(21, 4))) * 1000)
        if ey_raw < 0 or ey_raw > PW:
            ey = 0.0 if ey_raw < 0 else PW
            self.act(side, player, "clearance", "fail", (ax, ay), (ex, ey), flight_ms=flight, near=near)
            self.t += flight
            self.poss = opp
            self.dead(K.THROW_IN, opp, PL - ex, PW - ey)
            return
        ey = ey_raw
        if self.rng.random() < retain:
            receiver = self.nearest(side, ex, ey, True, exclude=player)
            self.act(side, player, "clearance", "success", (ax, ay), (ex, ey), flight_ms=flight, near=near)
            self.t += flight
            self.poss, self.carrier = side, receiver
            self.put_ball(side, ex, ey)
            return
        self.act(side, player, "clearance", "fail", (ax, ay), (ex, ey), flight_ms=flight, near=near)
        self.t += flight
        winner = self.nearest(opp, PL - ex, PW - ey, False)
        self.poss, self.carrier = opp, winner
        self.put_ball(opp, PL - ex, PW - ey)

    def own_goal(self, side: str, player: str, ax: float, ay: float) -> None:
        """``player`` (team ``side``) puts the ball into his own net, at x=0 of side's frame."""
        gy = float(self.rng.uniform(GOAL_LO + 0.5, GOAL_HI - 0.5))
        self.act(side, player, "clearance", "owngoal", (ax, ay), (0.0, gy), flight_ms=350, near=0.8)
        self.t += 350
        scorer_side = _other(side)
        self.teams[scorer_side].goals += 1
        self.goal_stoppage(scorer_side, PL, PW - gy)

    def shot(
        self,
        side: str,
        ax: float,
        ay: float,
        near: float,
        pressured: bool,
        *,
        header: bool = False,
        type_: str = "shot",
    ) -> None:
        opp = _other(side)
        shooter = self.carrier
        if type_ == "shot_penalty":
            q = 0.77
        elif type_ == "shot_freekick":
            q = 0.6 * xg(ax, ay, False, False)
        else:
            q = xg(ax, ay, header, pressured)
        finish = 0.85 + 0.6 * (self.players[shooter].rating - 0.5) + (HOME_EDGE if side == "home" else 0.0)
        keeper = 1.1 - 0.4 * (self.players[self.gk(opp)].rating - 0.5)
        p_goal = _clamp(q * finish * keeper, 0.005, 0.95)
        p_on = min(0.97, 0.27 + 0.9 * q) if type_ != "shot_penalty" else 0.94
        p_save = max(0.03, p_on - p_goal)
        p_block = 0.0 if type_ == "shot_penalty" else (0.20 + (0.12 if pressured else 0.0)) * (1 - p_on)
        speed = float(
            self.rng.normal(13, 3) if header else self.rng.normal(28 if type_ == "shot_penalty" else 27, 4)
        )
        speed = max(8.0, speed)
        bodypart = "head" if header else "foot"
        u = self.rng.random()
        gy = float(self.rng.uniform(GOAL_LO + 0.3, GOAL_HI - 0.3))
        dist_goal = math.hypot(PL - ax, gy - ay)
        flight = int(max(120, dist_goal / speed * 1000))
        if u < p_goal:
            self.act(
                side,
                shooter,
                type_,
                "success",
                (ax, ay),
                (PL, gy),
                flight_ms=flight,
                near=near,
                bodypart=bodypart,
            )
            self.t += flight
            self.teams[side].goals += 1
            self.goal_stoppage(side, PL, gy)
            return
        if u < p_goal + p_save:
            ex = PL - float(self.rng.uniform(0.3, 1.5))
            self.act(
                side,
                shooter,
                type_,
                "fail",
                (ax, ay),
                (ex, gy),
                flight_ms=flight,
                near=near,
                bodypart=bodypart,
            )
            self.t += flight
            g = self.gk(opp)
            self.act(
                opp,
                g,
                "keeper_save",
                "success",
                (PL - ex, PW - gy),
                (PL - ex, PW - gy),
                near=2.0,
                bodypart="other",
            )
            v = self.rng.random()
            if v < 0.35 and type_ != "shot_penalty":
                self.poss = side
                self.dead(K.CORNER, side, PL, 0.0 if gy < PW / 2 else PW)
            elif v < 0.42:
                rx, ry = float(self.rng.uniform(90, 98)), float(self.rng.normal(PW / 2, 8))
                ry = _clamp(ry, 10, PW - 10)
                self.t += int(self.rng.integers(400, 1200))
                if self.rng.random() < 0.4:
                    self.poss, self.carrier = side, self.nearest(side, rx, ry, True)
                    self.put_ball(side, rx, ry)
                else:
                    self.poss, self.carrier = opp, self.nearest(opp, PL - rx, PW - ry, False)
                    self.put_ball(opp, PL - rx, PW - ry)
            else:
                self.poss, self.carrier = opp, g
                self.put_ball(opp, PL - ex, PW - gy)
            return
        if u < p_goal + p_save + p_block:
            bd = float(self.rng.uniform(1.5, 6.0))
            ang = math.atan2(gy - ay, PL - ax)
            ex, ey = ax + bd * math.cos(ang), ay + bd * math.sin(ang)
            self.act(
                side,
                shooter,
                type_,
                "fail",
                (ax, ay),
                (ex, ey),
                flight_ms=max(80, int(bd / speed * 1000)),
                near=near,
                bodypart=bodypart,
            )
            self.t += max(80, int(bd / speed * 1000))
            v = self.rng.random()
            if v < 0.45:
                self.poss = side
                self.dead(K.CORNER, side, PL, 0.0 if ay < PW / 2 else PW)
            elif v < 0.62:
                self.carrier = self.nearest(side, ex, ey, True)
                self.put_ball(side, ex, ey)
            else:
                self.poss, self.carrier = opp, self.nearest(opp, PL - ex, PW - ey, False)
                self.put_ball(opp, PL - ex, PW - ey)
            return
        # Off target: wide of a post (or over, which the 2-D frame records as wide).
        wy = float(
            self.rng.choice(
                [self.rng.uniform(GOAL_LO - 7, GOAL_LO - 0.3), self.rng.uniform(GOAL_HI + 0.3, GOAL_HI + 7)]
            )
        )
        self.act(
            side, shooter, type_, "fail", (ax, ay), (PL, wy), flight_ms=flight, near=near, bodypart=bodypart
        )
        self.t += flight
        self.poss = opp
        self.dead(K.GOAL_KICK, opp, 5.5, PW / 2 + (9.16 if wy > PW / 2 else -9.16))

    # ----- stoppages ---------------------------------------------------------------------------

    def goal_stoppage(self, scorer: str, ax: float, ay: float) -> None:
        conceding = _other(scorer)
        self.poss = conceding
        self.dead(K.GOAL, conceding, PL - ax, PW - ay, var=0.2)

    def injury_stoppage(self, side: str, ax: float, ay: float) -> None:
        victim_side = side if self.rng.random() < 0.5 else _other(side)
        team = self.teams[victim_side]
        outfield = sorted(p for p, s in team.slots.items() if s.role != "GK")
        victim = outfield[int(self.rng.integers(len(outfield)))]
        self.dead(K.INJURY, side, ax, ay, injured=victim, injured_side=victim_side)

    def dead(
        self,
        kind: K,
        restart_side: str,
        ax: float,
        ay: float,
        *,
        card=None,
        injured: str | None = None,
        injured_side: str | None = None,
        var: float = 0.0,
    ) -> None:
        """A stoppage: ``stoppage_start``, what happens inside it, ``restart``, then the restart action.

        (ax, ay) is the restart spot in ``restart_side``'s attacking frame.
        """
        t0 = self.t
        dangerous = kind is K.FREE_KICK and math.hypot(PL - ax, PW / 2 - ay) < 32
        dt = FREE_KICK_DANGEROUS if dangerous else DEAD_TIME[kind]
        main = dt.draw(self.rdead)
        expected = dt.median_ms
        inner: list[tuple[str, tuple]] = []
        if card:
            inner.append(("card", card))
            expected += EXTRA["card"].median_ms
        if card and card[2] is CardColor.RED and self.rng.random() < 0.3:
            inner.append(("var", ("red_card",)))
        if kind is K.PENALTY and self.rng.random() < 0.3:
            inner.append(("var", ("penalty",)))
        if var and self.rng.random() < var:
            inner.append(("var", ("goal",)))
        inj_side = injured_side or restart_side
        if injured:
            inner.append(("injury", (inj_side, injured)))
            expected += EXTRA["injury"].median_ms
        subs = self.plan_subs(kind, injured, inj_side, card)
        for sub in subs:
            inner.append(("sub", sub))
            expected += EXTRA["substitution"].median_ms
        bx, by = self.to_b(restart_side, ax, ay)
        self.emit(
            StoppageStart,
            kind=kind,
            restart_team_id=self.teams[restart_side].team_id,
            expected_dead_ms=expected,
            x=bx,
            y=by,
        )
        self.bx, self.by = bx, by
        cursor = t0 + int(self.rng.integers(2_000, 6_000))
        added = 0
        for what, data in inner:
            self.t = max(self.t, min(cursor, t0 + main + added - 1))
            if what == "card":
                cside, cplayer, color, reason = data
                self.emit(Card, cside, cplayer, color=color, reason=reason)
                if color is CardColor.YELLOW:
                    self.teams[cside].yellow.add(cplayer)
                else:
                    self.send_off(cside, cplayer)
                d = EXTRA["card"].draw(self.rdead)
            elif what == "var":
                self.emit(VarCheck, subject=data[0], outcome="stands")
                d = EXTRA["var"].draw(self.rdead)
            elif what == "injury":
                self.emit(Injury, data[0], data[1])
                d = EXTRA["injury"].draw(self.rdead)
            else:
                sside, off, on = data
                self.emit(Substitution, sside, off, off_id=off, on_id=on)
                self.apply_sub(sside, off, on)
                d = EXTRA["substitution"].draw(self.rdead)
            added += d
            cursor = self.t + d
        total = main + added
        self.t = t0 + total
        self.extra += added + (main if kind in (K.GOAL, K.PENALTY, K.INJURY) else int(0.08 * main))
        self._s = {}
        self.emit(Restart, kind=kind)
        self.restart(kind, restart_side, ax, ay)

    def restart(self, kind: K, side: str, ax: float, ay: float) -> None:
        self.poss = side
        self.put_ball(side, ax, ay)
        s = self.settings(side)
        if kind is K.GOAL:
            self.kickoff(side)
            return
        if kind is K.THROW_IN:
            self.carrier = self.nearest(side, ax, ay, True, roles=("DF", "MF", "FW"))
            self.pass_(side, ax, ay, None, False, mode="short", type_="throw_in", bonus=-0.3)
            return
        if kind is K.GOAL_KICK:
            self.carrier = self.gk(side)
            long_kick = self.rng.random() < 0.25 + 0.6 * s["directness"]
            self.pass_(
                side,
                ax,
                ay,
                None,
                False,
                mode="long" if long_kick else "short",
                type_="goal_kick",
                bonus=1.2 if not long_kick else 0.0,
            )
            return
        if kind is K.CORNER:
            self.carrier = self.set_piece_taker(side)
            if self.rng.random() < 0.82:
                self.cross(side, ax, ay, None, False, type_="corner_crossed")  # type: ignore[arg-type]
            else:
                self.pass_(side, ax, ay, None, False, mode="short", type_="corner_short", bonus=1.0)
            return
        if kind is K.PENALTY:
            self.carrier = self.set_piece_taker(side, roles=("FW",))
            self.shot(side, PL - 11.0, PW / 2, 11.0, False, type_="shot_penalty")
            return
        if kind in (K.FREE_KICK, K.OFFSIDE, K.INJURY):
            d_goal = math.hypot(PL - ax, PW / 2 - ay)
            if kind is K.FREE_KICK and d_goal < 30 and abs(ay - PW / 2) < 18 and self.rng.random() < 0.22:
                self.carrier = self.set_piece_taker(side)
                self.shot(side, ax, ay, 9.15, False, type_="shot_freekick")
                return
            if kind is K.FREE_KICK and ax > 68 and self.rng.random() < 0.6:
                self.carrier = self.set_piece_taker(side)
                self.cross(side, ax, ay, None, False, type_="freekick_crossed")  # type: ignore[arg-type]
                return
            self.carrier = self.nearest(side, ax, ay, True)
            type_ = "pass" if kind is K.INJURY else "freekick_short"
            self.pass_(side, ax, ay, None, False, mode="short", type_=type_, bonus=1.2)
            return
        raise AssertionError(f"no restart for {kind}")

    def set_piece_taker(self, side: str, roles: tuple[str, ...] = ("MF", "FW")) -> str:
        team = self.teams[side]
        pool = [pid for pid, s in team.slots.items() if s.role in roles] or [
            p for p in team.slots if team.slots[p].role != "GK"
        ]
        return max(pool, key=lambda p: (self.players[p].rating, p))

    # ----- cards and substitutions -------------------------------------------------------------

    def send_off(self, side: str, player: str) -> None:
        team = self.teams[side]
        team.gone.add(player)
        del team.slots[player]
        if self.carrier == player:
            self.carrier = next(iter(team.slots))
        n = len(team.slots)
        players = [self.players[p] for p in team.slots]
        if n == 10:
            team.formation = TEN_MAN_FOR[team.club.formation]
            team.slots = assign(TEN_MAN[team.formation], players)
        else:
            # Fewer than ten: keep the remaining slots, dropping the most advanced one each time.
            base = TEN_MAN[TEN_MAN_FOR[team.club.formation]]
            keep = sorted(base, key=lambda s: s.x)[:n]
            team.slots = assign(tuple(keep), players)
        mag = float(self.rng.uniform(0.25, 0.35))
        self.start_change(
            side,
            CauseLabel.RED_CARD_REORG,
            self.fitted(side, CauseLabel.RED_CARD_REORG, mag),
            60_000,
            delay_ms=int(self.rng.integers(5_000, 25_000)),
        )

    def plan_subs(self, kind: K, injured: str | None, inj_side: str, card) -> list[tuple[str, str, str]]:
        """Substitutions made in this stoppage, as (side, off_id, on_id)."""
        if kind in (K.PENALTY,):
            return []
        out: list[tuple[str, str, str]] = []
        m = self.minute()
        for side in ("home", "away"):
            team = self.teams[side]
            want = 0
            if injured and side == inj_side and injured in team.slots and self.rng.random() < 0.55:
                want = 1
            elif m >= 55:
                p = 0.05 if m < 75 else 0.11
                if self.rng.random() < p:
                    want = 1 if self.rng.random() < 0.6 else 2
            want = min(want, 5 - team.subs_used)
            if want <= 0 or team.windows_used >= 3:
                continue
            team.windows_used += 1
            # A player being sent off in this stoppage cannot also be substituted.
            taken: set[str] = {card[1]} if card and card[2] is CardColor.RED else set()
            for i in range(want):
                off = injured if (i == 0 and injured and side == inj_side and injured in team.slots) else None
                off = off or self.pick_off(team, taken)
                on = self.pick_on(team, off, taken)
                if off is None or on is None:
                    break
                taken.update((off, on))
                out.append((side, off, on))
                team.subs_used += 1
        return out

    def pick_off(self, team: Team, taken: set[str]) -> str | None:
        cands = [
            p
            for p, s in team.slots.items()
            if s.role != "GK" and p not in taken and team.on_minute.get(p, 0) < 50
        ]
        if not cands:
            return None
        return min(cands, key=lambda p: (self.players[p].rating - (0.1 if p in team.yellow else 0.0), p))

    def pick_on(self, team: Team, off: str | None, taken: set[str]) -> str | None:
        if off is None:
            return None
        role = team.slots[off].role if off in team.slots else self.players[off].position
        pool = [p for p in team.bench if p.player_id not in taken]
        same = [p for p in pool if p.position == role] or [p for p in pool if p.position != "GK"]
        return max(same, key=lambda p: (p.rating, p.player_id)).player_id if same else None

    def substitute(self, side: str, n: int, *, window: bool) -> None:
        team = self.teams[side]
        taken: set[str] = set()
        for _ in range(min(n, 5 - team.subs_used)):
            off = self.pick_off(team, taken)
            on = self.pick_on(team, off, taken)
            if off is None or on is None:
                return
            taken.update((off, on))
            self.emit(Substitution, side, off, off_id=off, on_id=on)
            self.apply_sub(side, off, on)
            team.subs_used += 1
        if window:
            team.windows_used += 1

    def apply_sub(self, side: str, off: str, on: str) -> None:
        team = self.teams[side]
        team.slots[on] = team.slots.pop(off)
        team.gone.add(off)
        team.bench = [p for p in team.bench if p.player_id != on]
        team.on_minute[on] = self.minute()
        if self.carrier == off:
            self.carrier = on


def simulate(seed: int) -> MatchResult:
    """Simulate the match for one seed (see ``earshot.world.clubs.fixture_for_seed``)."""
    return MatchSim(seed).run()
