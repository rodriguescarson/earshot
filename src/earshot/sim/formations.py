"""Formation slots in the attacking frame (own goal at x=0), and the 10-man versions.

A slot is a nominal position in a neutral shape (defensive line near x=22). The match loop
moves the whole block with the ball and with the team's line height, press height and width.
"""

from __future__ import annotations

from dataclasses import dataclass

from earshot.contract.match import Position


@dataclass(frozen=True)
class Slot:
    role: Position
    x: float
    y: float
    side: str


def _s(role: Position, x: float, y: float) -> Slot:
    side = "R" if y < 24 else "L" if y > 44 else "C"
    return Slot(role, x, y, side)


GK = _s("GK", 5, 34)
BACK4 = (_s("DF", 25, 9), _s("DF", 21, 26), _s("DF", 21, 42), _s("DF", 25, 59))
BACK3 = (_s("DF", 22, 18), _s("DF", 20, 34), _s("DF", 22, 50))
BACK5 = (_s("DF", 30, 6), _s("DF", 22, 20), _s("DF", 20, 34), _s("DF", 22, 48), _s("DF", 30, 62))

FORMATIONS: dict[str, tuple[Slot, ...]] = {
    "4-3-3": (GK, *BACK4, _s("MF", 38, 34), _s("MF", 46, 22), _s("MF", 46, 46),
              _s("FW", 64, 10), _s("FW", 70, 34), _s("FW", 64, 58)),
    "4-2-3-1": (GK, *BACK4, _s("MF", 38, 26), _s("MF", 38, 42), _s("MF", 56, 34),
                _s("FW", 60, 10), _s("FW", 60, 58), _s("FW", 70, 34)),
    "4-4-2": (GK, *BACK4, _s("MF", 48, 9), _s("MF", 43, 27), _s("MF", 43, 41), _s("MF", 48, 59),
              _s("FW", 67, 28), _s("FW", 67, 40)),
    "3-5-2": (GK, *BACK3, _s("MF", 46, 7), _s("MF", 42, 22), _s("MF", 38, 34), _s("MF", 42, 46),
              _s("MF", 46, 61), _s("FW", 67, 28), _s("FW", 67, 40)),
    "3-4-3": (GK, *BACK3, _s("MF", 46, 7), _s("MF", 41, 27), _s("MF", 41, 41), _s("MF", 46, 61),
              _s("FW", 64, 14), _s("FW", 70, 34), _s("FW", 64, 54)),
    "5-3-2": (GK, *BACK5, _s("MF", 42, 20), _s("MF", 40, 34), _s("MF", 42, 48),
              _s("FW", 66, 28), _s("FW", 66, 40)),
}  # fmt: skip

TEN_MAN: dict[str, tuple[Slot, ...]] = {
    "4-4-1": (GK, *BACK4, _s("MF", 45, 10), _s("MF", 41, 27), _s("MF", 41, 41), _s("MF", 45, 58),
              _s("FW", 64, 34)),
    "3-4-2": (GK, *BACK3, _s("MF", 44, 8), _s("MF", 40, 27), _s("MF", 40, 41), _s("MF", 44, 60),
              _s("FW", 64, 28), _s("FW", 64, 40)),
    "5-3-1": (GK, *BACK5, _s("MF", 40, 20), _s("MF", 38, 34), _s("MF", 40, 48), _s("FW", 62, 34)),
}  # fmt: skip

TEN_MAN_FOR = {"4-3-3": "4-4-1", "4-2-3-1": "4-4-1", "4-4-2": "4-4-1", "3-5-2": "3-4-2", "3-4-3": "3-4-2",
               "5-3-2": "5-3-1"}  # fmt: skip

for _name, _slots in FORMATIONS.items():
    assert len(_slots) == 11, _name
for _name, _slots in TEN_MAN.items():
    assert len(_slots) == 10, _name


def assign(slots: tuple[Slot, ...], players: list, key=lambda p: p.rating) -> dict[str, Slot]:
    """Give each slot one player: same role first, preferred side breaking ties, best rating first.

    ``players`` are objects with ``player_id``, ``position``, ``side`` and ``rating``. When a role
    runs short (a defender sent off), the nearest role fills in (MF covers DF, FW covers MF).
    """
    fallback: dict[str, tuple[str, ...]] = {
        "GK": ("GK", "DF", "MF", "FW"),
        "DF": ("DF", "MF", "FW"),
        "MF": ("MF", "DF", "FW"),
        "FW": ("FW", "MF", "DF"),
    }
    free = sorted(players, key=lambda p: (-key(p), p.player_id))
    out: dict[str, Slot] = {}
    for slot in sorted(slots, key=lambda s: ("GK", "DF", "FW", "MF").index(s.role)):
        for role in fallback[slot.role]:
            pool = [p for p in free if p.position == role]
            if pool:
                pick = next((p for p in pool if p.side == slot.side), pool[0])
                out[pick.player_id] = slot
                free.remove(pick)
                break
    return out
