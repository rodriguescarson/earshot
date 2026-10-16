"""Foundry's built-in fluency and coherence evaluators over every template, per language.

The template-quality check for Arabic and Hindi (and the same pass for English and Spanish). The
sentences judged are the ones the product airs: whole development matches go through the live
broadcaster on the virtual clock (``earshot.eval.run``) with a listening group per language and
mode (plus a followed club, player and focus stat, so those lines appear too), and one line is
kept per template, mode and language. A template no match produced is rendered from the catalog
with slot values seen in that language, and marked ``rendered``. Each distinct sentence is judged
once.

Judge: the ``fast`` deployment (``EARSHOT_FAST_DEPLOYMENT``) through ``azure-ai-evaluation``,
keyless (Entra). Calls are paced under the hourly token alert. Scores are 1-5; the evaluators'
own pass threshold is 3.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

from earshot.contract import Language, Line, ListeningMode, OutputMode
from earshot.contract.line import FocusStat

LANGUAGE_NAMES = {Language.EN: "English", Language.ES: "Spanish", Language.AR: "Arabic", Language.HI: "Hindi"}
PASS = 3


def collect(seeds: Sequence[int]) -> dict[tuple[str, str, str], Line]:
    """(language, template_id, mode) -> the first line the product offered for it."""
    from earshot.eval.run import LatencyAnalyst, LatencyModel, _virtual_match_class
    from earshot.live.broadcast import GroupKey
    from earshot.rng import stream
    from earshot.sim.match import simulate
    from earshot.world.localize import roster

    out: dict[tuple[str, str, str], Line] = {}
    lat = LatencyModel.provisional_default()
    for seed in seeds:
        r = simulate(seed)
        latin = roster(Language.EN)
        teams = {r.info.home.team_id, r.info.away.team_id}
        clubs = [c for c in latin.clubs if c.team_id in teams]
        players = [p for p in latin.players if p.team_id in teams]
        rng = stream(seed, "eval", "templates")
        vm = _virtual_match_class()(
            r.info, list(r.events), clubs, players, latency=lat, rng=rng, analyst=LatencyAnalyst(lat, rng)
        )
        home = r.info.home.team_id
        fw = next(
            (p for p in r.info.home.starting if vm.roster.players[p].position == "FW"),
            r.info.home.starting[-1],
        )
        for lang in Language:
            for mode in ListeningMode:
                vm.join(GroupKey(r.info.match_id, lang, mode, OutputMode.APP_VOICE))
                stat = (
                    FocusStat.SHOT_SPEED if mode is ListeningMode.ANALYST else None
                )  # stats are Analyst-only
                vm.join(GroupKey(r.info.match_id, lang, mode, OutputMode.ONE_EAR, home, fw, stat))
        asyncio.run(vm.play())
        for line in vm.offered:
            out.setdefault((line.language.value, line.template_id, line.listening_mode.value), line)
    return out


def items(seeds: Sequence[int]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One sentence per template, mode and language, and coverage."""
    from earshot.agents.catalog import load, placeholders

    seen = collect(seeds)
    slot_values: dict[str, dict[str, Any]] = defaultdict(dict)
    for (lang, _, _), line in seen.items():
        for k, v in line.slots.items():
            slot_values[lang].setdefault(k, v)
    rows: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    for lang in Language:
        cat = load(lang)
        aired = rendered = missing = 0
        for tid, by_mode in sorted(cat.templates.items()):
            for mode_value in sorted(by_mode):
                line = seen.get((lang.value, tid, mode_value))
                if line is not None:
                    rows.append({"language": lang.value, "template_id": tid, "mode": mode_value,
                                 "text": line.text, "how": "aired"})  # fmt: skip
                    aired += 1
                    continue
                need = placeholders(by_mode[mode_value])
                values = slot_values[lang.value]
                if need - values.keys():
                    missing += 1
                    continue
                text = cat.render(tid, ListeningMode(mode_value), {k: values[k] for k in need})
                rows.append({"language": lang.value, "template_id": tid, "mode": mode_value,
                             "text": text, "how": "rendered"})  # fmt: skip
                rendered += 1
        coverage[lang.value] = {"aired": aired, "rendered": rendered, "not_judged": missing}
    compose_prefixes(rows)
    return rows, coverage


def compose_prefixes(rows: list[dict[str, Any]]) -> None:
    """A prefix template (``late.1``, "Earlier: ") never airs alone: judge it on a real line."""
    for row in rows:
        if not row["text"].rstrip().endswith(":"):
            continue
        host = next(
            (r for r in rows if r["language"] == row["language"] and r["how"] == "aired"
             and r["template_id"].startswith("goal")),
            None,
        )  # fmt: skip
        if host is not None:
            row["text"] = row["text"].rstrip() + " " + host["text"]
            row["how"] = "composed"


class Judge:
    def __init__(self, tokens_per_hour: int, sleep: Callable[[float], None] = time.sleep) -> None:
        from azure.ai.evaluation import AzureOpenAIModelConfiguration, CoherenceEvaluator, FluencyEvaluator
        from azure.identity import DefaultAzureCredential

        cfg = AzureOpenAIModelConfiguration(
            azure_endpoint=os.environ["EARSHOT_AI_ENDPOINT"],
            azure_deployment=os.environ.get("EARSHOT_FAST_DEPLOYMENT", "fast"),
            api_version="2025-04-01-preview",
        )
        cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self.fluency = FluencyEvaluator(cfg, credential=cred, is_reasoning_model=True)
        self.coherence = CoherenceEvaluator(cfg, credential=cred, is_reasoning_model=True)
        self.tokens_per_hour, self.sleep = tokens_per_hour, sleep
        self.spent: list[tuple[float, int]] = []

    def _pace(self) -> None:
        while True:
            now = time.monotonic()
            self.spent = [(t, n) for t, n in self.spent if now - t < 3_600]
            if sum(n for _, n in self.spent) + 2_200 <= self.tokens_per_hour:
                return
            self.sleep(30)

    def score(self, text: str, language: str) -> dict[str, Any]:
        self._pace()
        query = f"One line of live football commentary in {language}, read aloud to a blind listener."
        out: dict[str, Any] = {}
        for name, ev, kw in (
            ("fluency", self.fluency, {"response": text}),
            ("coherence", self.coherence, {"query": query, "response": text}),
        ):
            try:
                r: dict[str, Any] = dict(ev(**kw))
            except Exception as e:  # one failed judge call is recorded, never fatal
                out[name] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
                continue
            props = r.get(f"{name}_properties")
            props = props if isinstance(props, dict) else {}
            self.spent.append((time.monotonic(), int(props.get("total_tokens", 1_100))))
            out[name] = {"score": r.get(name), "reason": r.get(f"{name}_reason")}
        return out


def failed(row: dict[str, Any]) -> bool:
    return any(not isinstance(row.get(k), dict) or "error" in row[k] for k in ("fluency", "coherence"))


def run(
    seeds: Sequence[int], tokens_per_hour: int = 200_000, previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Judge every sentence; with ``previous`` (an earlier result), keep its scores and judge only
    the rows that errored or are new (for example after the kill switch cut a run short)."""
    rows, coverage = items(seeds)
    kept: dict[tuple[str, str], dict[str, Any]] = {}
    for old in (previous or {}).get("rows", []):
        if not failed(old):
            kept[(old["language"], old["text"])] = {"fluency": old["fluency"], "coherence": old["coherence"]}
    judge = Judge(tokens_per_hour)
    cache: dict[tuple[str, str], dict[str, Any]] = dict(kept)
    for row in rows:
        key = (row["language"], row["text"])
        if key not in cache:
            cache[key] = judge.score(row["text"], LANGUAGE_NAMES[Language(row["language"])])
        row |= cache[key]
    return {"seeds": list(seeds), "coverage": coverage, "summary": summarize(rows), "rows": rows,
            "judge_calls": 2 * (len(cache) - len(kept)), "kept_from_previous": len(kept),
            "tokens": sum(n for _, n in judge.spent)}  # fmt: skip


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for lang in sorted({r["language"] for r in rows}):
        mine = [r for r in rows if r["language"] == lang]
        per: dict[str, Any] = {"sentences": len(mine)}
        for name in ("fluency", "coherence"):
            scores = [
                r[name]["score"] for r in mine if isinstance(r.get(name), dict) and r[name].get("score")
            ]
            per[name] = {
                "judged": len(scores),
                "mean": round(statistics.mean(scores), 2) if scores else None,
                "min": min(scores) if scores else None,
                "below_pass": sum(1 for s in scores if s < PASS),
                "errors": sum(1 for r in mine if isinstance(r.get(name), dict) and "error" in r[name]),
            }
        worst = sorted(
            (r for r in mine if isinstance(r.get("fluency"), dict) and r["fluency"].get("score")),
            key=lambda r: (r["fluency"]["score"], (r.get("coherence") or {}).get("score") or 9),
        )[:5]
        per["lowest"] = [
            {"template_id": r["template_id"], "mode": r["mode"], "text": r["text"],
             "fluency": r["fluency"]["score"], "coherence": (r.get("coherence") or {}).get("score"),
             "reason": r["fluency"].get("reason")}
            for r in worst
        ]  # fmt: skip
        out[lang] = per
    return out
