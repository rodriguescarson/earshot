"""Player frames at 10 Hz for the render match only (CEO-4), and gate G8.

The engine is events-first, so frames are computed after the events. While the match is
re-simulated, the team shape the engine itself used is recorded at every action (recording draws
no random numbers, so the events are byte-identical). Each player then steers towards his next
recorded position, with the actor of each action steering to the ball, under a speed cap and an
acceleration cap. Off-ball players add a slow, deterministic drift within their zone, the
scanning and repositioning that real players do between actions.

G8 (physical, pre-registered in EVALUATION.md): median outfield distance 9.5-12 km; sprint
distance (above 7 m/s, 25.2 km/h) 150-450 m; ball within 1.5 m of the actor at 99% of actions;
speed never above 9.5 m/s; acceleration never above 8 m/s^2.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from earshot.contract.events import Action, Card, Event, PeriodEnd, Restart, StoppageKind, Substitution
from earshot.pitch import PITCH_LENGTH, PITCH_WIDTH
from earshot.sim.match import MatchResult, MatchSim

HZ = 10
DT = 1.0 / HZ
V_MAX = 9.3
A_MAX = 7.5
SPRINT = 7.0
DRIFT_M = 13.0
LOOKAHEAD_MS = 4_000
"""An actor heads straight for the ball once his action is this close."""


class RecordingSim(MatchSim):
    """MatchSim that remembers both teams' shapes (broadcast frame) at every on-ball action."""

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self.shapes: list[tuple[int, int, dict[str, tuple[float, float]]]] = []

    def emit(self, cls, side=None, player=None, **kw) -> None:
        super().emit(cls, side, player, **kw)
        if cls is Action:
            snap: dict[str, tuple[float, float]] = {}
            for s in ("home", "away"):
                for pid, (ax, ay) in self.positions(s, s == self.poss).items():
                    snap[pid] = self.to_b(s, ax, ay)
            ev = self.events[-1]
            assert isinstance(ev, Action)
            if player:
                snap[player] = (ev.start_x, ev.start_y)
            self.shapes.append((ev.wall_ms, ev.period_id, snap))


@dataclass
class Frames:
    t_ms: np.ndarray
    """Wall time of each frame (both periods; the half-time interval is skipped)."""
    period: np.ndarray
    players: dict[str, np.ndarray]
    """player_id -> (n, 2) positions; NaN while the player is off the pitch."""
    events: list[Event]
    result: MatchResult


def _drift(pid: str, t_s: np.ndarray) -> np.ndarray:
    """A smooth, deterministic wander of up to DRIFT_M metres (two slow sines per axis, phase from the id)."""
    h = sum(ord(c) * (i + 1) for i, c in enumerate(pid))
    p1, p2, p3, p4 = (h % 97) / 97 * 6.28, (h % 89) / 89 * 6.28, (h % 83) / 83 * 6.28, (h % 79) / 79 * 6.28
    dx = DRIFT_M * (0.6 * np.sin(t_s / 11.0 + p1) + 0.4 * np.sin(t_s / 4.5 + p2))
    dy = DRIFT_M * (0.6 * np.sin(t_s / 13.0 + p3) + 0.4 * np.sin(t_s / 5.3 + p4))
    return np.stack([dx, dy], axis=1)


def render_frames(seed: int) -> Frames:
    sim = RecordingSim(seed)
    result = sim.run()
    events = result.events
    # Period spans and who is on the pitch when.
    spans: list[tuple[int, int, int]] = []
    starts = {1: 0}
    for e in events:
        if isinstance(e, Restart) and e.kind is StoppageKind.HALF_TIME:
            starts[2] = e.wall_ms
        if isinstance(e, PeriodEnd):
            spans.append((e.period_id, starts[e.period_id], e.wall_ms))
    t_list, p_list = [], []
    for pid_, a, b in spans:
        ts = np.arange(a, b + 1, int(DT * 1000))
        t_list.append(ts)
        p_list.append(np.full(len(ts), pid_))
    t_ms = np.concatenate(t_list)
    period = np.concatenate(p_list)
    on_from: dict[str, int] = {pid: 0 for pid in (*result.info.home.starting, *result.info.away.starting)}
    off_at: dict[str, int] = {}
    for e in events:
        if isinstance(e, Substitution):
            off_at[e.off_id] = e.wall_ms
            on_from[e.on_id] = e.wall_ms
        elif isinstance(e, Card) and e.color == "red" and e.player_id:
            off_at[e.player_id] = e.wall_ms
    shape_t = np.array([s[0] for s in sim.shapes], dtype=np.int64)
    players: dict[str, np.ndarray] = {}
    for pid, t_on in on_from.items():
        t_off = off_at.get(pid, int(t_ms[-1]) + 1)
        keys = [(t, xy[pid]) for (t, _, xy) in sim.shapes if pid in xy and t_on <= t < t_off]
        out = np.full((len(t_ms), 2), np.nan)
        if not keys:
            players[pid] = out
            continue
        kt = np.array([k[0] for k in keys], dtype=np.int64)
        kxy = np.array([k[1] for k in keys])
        drift = _drift(pid, t_ms / 1000.0)
        pos = kxy[0].copy()
        vel = np.zeros(2)
        acts = sorted(
            (e.wall_ms, e.start_x, e.start_y) for e in events if isinstance(e, Action) and e.player_id == pid
        )
        act_t = np.array([a[0] for a in acts], dtype=np.int64)
        act_xy = np.array([[a[1], a[2]] for a in acts]) if acts else np.zeros((0, 2))
        for i, t in enumerate(t_ms):
            if not (t_on <= t < t_off):
                continue
            if i > 0 and period[i] != period[i - 1]:
                vel[:] = 0.0  # ends switch at half-time: the player walks over during the interval
                j0 = int(np.searchsorted(kt, t, side="left"))
                pos = kxy[min(j0, len(kt) - 1)].copy()
            a = int(np.searchsorted(act_t, t, side="left"))
            j = int(np.searchsorted(kt, t, side="left"))
            if a < len(act_t) and act_t[a] - t <= LOOKAHEAD_MS:
                target, eta = act_xy[a], max(DT, (act_t[a] - t) / 1000.0)  # going to the ball
            elif j >= len(kt):
                target, eta = kxy[-1] + drift[i], 1.0
            else:
                target, eta = kxy[j] + drift[i], max(DT, (kt[j] - t) / 1000.0)
            target = np.clip(target, [1.0, 1.0], [PITCH_LENGTH - 1.0, PITCH_WIDTH - 1.0])
            gap = target - pos
            dist = float(np.hypot(*gap))
            # Arrive, don't overshoot: no faster than the time left asks, the cap allows, or a stop
            # within the acceleration cap still permits.
            speed = min(dist / max(eta, 0.25), V_MAX, math.sqrt(2 * A_MAX * 0.5 * dist))
            want = gap / dist * speed if dist > 1e-9 else np.zeros(2)
            dv = want - vel
            acc = float(np.hypot(*dv)) / DT
            if acc > A_MAX:
                dv *= A_MAX / acc
            vel = vel + dv
            nxt = pos + vel * DT
            # Targets sit inside the pitch, so players brake within the cap before a line; this
            # clamp is only a guard and should never bind.
            pos = np.clip(nxt, [0.0, 0.0], [PITCH_LENGTH, PITCH_WIDTH])
            out[i] = pos
        players[pid] = out
    _ = shape_t
    return Frames(t_ms, period, players, events, result)


def physical(fr: Frames) -> dict:
    """The G8 numbers for one match."""
    positions = {p: fr.result.info.home.team_id for p in fr.result.info.home.starting}
    outfield = []
    sprint = []
    vmax = amax = 0.0
    for pid, xy in fr.players.items():
        ok = ~np.isnan(xy[:, 0])
        if ok.sum() < 2:
            continue
        seg = np.diff(xy, axis=0)
        valid = ok[1:] & ok[:-1] & (fr.period[1:] == fr.period[:-1])
        step = np.hypot(seg[:, 0], seg[:, 1])
        step = np.where(valid, step, 0.0)
        v = step / DT
        vmax = max(vmax, float(v.max()))
        dvel = np.diff(seg / DT, axis=0)
        vv = valid[1:] & valid[:-1]
        a = np.where(vv, np.hypot(dvel[:, 0], dvel[:, 1]) / DT, 0.0)
        amax = max(amax, float(a.max()) if len(a) else 0.0)
        is_gk = pid.endswith(("-01", "-13", "-31"))
        full_match = ok.mean() > 0.95
        if not is_gk and full_match:
            outfield.append(float(step.sum()))
            sprint.append(float(step[v > SPRINT].sum()))
    near = []
    idx = {int(t): i for i, t in enumerate(fr.t_ms)}
    for e in fr.events:
        if isinstance(e, Action) and e.player_id in fr.players:
            i = idx.get(int(round(e.wall_ms / 100.0) * 100))
            if i is None:
                continue
            p = fr.players[e.player_id][i]
            if not math.isnan(p[0]):
                near.append(math.hypot(p[0] - e.start_x, p[1] - e.start_y))
    near_arr = np.array(near)
    _ = positions
    return {
        "outfield_players": len(outfield),
        "median_outfield_km": round(float(np.median(outfield)) / 1000, 2) if outfield else None,
        "median_sprint_m": round(float(np.median(sprint)), 1) if sprint else None,
        "share_actions_ball_within_1_5m": round(float((near_arr <= 1.5).mean()), 4)
        if len(near_arr)
        else None,
        "max_speed_ms": round(vmax, 2),
        "max_accel_ms2": round(amax, 2),
    }


def g8(fr: Frames) -> tuple[bool, dict]:
    p = physical(fr)
    ok = (
        p["median_outfield_km"] is not None
        and 9.5 <= p["median_outfield_km"] <= 12.0
        and 150 <= (p["median_sprint_m"] or 0) <= 450
        and (p["share_actions_ball_within_1_5m"] or 0) >= 0.99
        and p["max_speed_ms"] <= 9.5
        and p["max_accel_ms2"] <= 8.0
    )
    return ok, p
