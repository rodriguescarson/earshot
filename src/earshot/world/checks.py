"""Compliance checks on every generated name (design "Compliance", R3-30).

- **People** (players, coaches, referees): no exact full-name match with a real human on Wikidata
  who is an association football player, coach or referee, or whose sport is association football.
- **Places** (club names, stadium names and their words): no exact match with a Wikidata human
  settlement (instances of Q486972 or its subclasses), plus the banned-word rules in
  ``earshot.world.names``.

Wikidata is queried live through its public SPARQL endpoint and used only as a filter: nothing
from it is stored or redistributed, only our own names and the ids they matched. The design also
named the GeoNames cities list (CC BY); that needs a file download, which this check does not do
(see the report's ``limits``).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from earshot.world.clubs import world

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = (
    "earshot-name-check/0.1 (hackathon entry; filters synthetic names against real people and places)"
)
BATCH = 200
PAUSE_S = 65.0
"""Wikidata's query service may limit clients to one request a minute; stay under it."""
FOOTBALL_OCCUPATIONS = ("Q937857", "Q628099", "Q859528")  # player, coach, referee
ASSOCIATION_FOOTBALL = "Q2736"
HUMAN_SETTLEMENT = "Q486972"


@dataclass(frozen=True)
class Hit:
    name: str
    kind: str
    wikidata: str
    detail: str


def _lit(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"@en'


def sparql(query: str) -> list[dict]:
    data = urllib.parse.urlencode({"query": query, "format": "json"}).encode()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json",
               "Content-Type": "application/x-www-form-urlencoded"}  # fmt: skip
    req = urllib.request.Request(ENDPOINT, data=data, headers=headers)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)["results"]["bindings"]
        except Exception:  # retried after a pause; the last failure propagates
            if attempt == 3:
                raise
            time.sleep(PAUSE_S)
    return []


def people_query(names: list[str]) -> str:
    values = " ".join(_lit(n) for n in names)
    occ = " ".join(f"wd:{q}" for q in FOOTBALL_OCCUPATIONS)
    return f"""SELECT ?item ?label (GROUP_CONCAT(DISTINCT ?occL; separator=", ") AS ?occs) WHERE {{
  VALUES ?label {{ {values} }}
  ?item rdfs:label ?label ; wdt:P31 wd:Q5 .
  {{ ?item wdt:P106 ?o . VALUES ?o {{ {occ} }} }} UNION {{ ?item wdt:P641 wd:{ASSOCIATION_FOOTBALL} }}
  OPTIONAL {{ ?item wdt:P106 ?oc . ?oc rdfs:label ?occL . FILTER(LANG(?occL) = "en") }}
}} GROUP BY ?item ?label"""


def places_query(words: list[str]) -> str:
    values = " ".join(_lit(w) for w in words)
    return f"""SELECT ?item ?label ?countryLabel WHERE {{
  VALUES ?label {{ {values} }}
  ?item rdfs:label ?label ; wdt:P31/wdt:P279* wd:{HUMAN_SETTLEMENT} .
  OPTIONAL {{ ?item wdt:P17 ?country . ?country rdfs:label ?countryLabel .
             FILTER(LANG(?countryLabel) = "en") }}
}}"""


def person_names() -> list[str]:
    w = world()
    names = [p.name.full for c in w.clubs for p in c.players] + [c.coach.full for c in w.clubs]
    return sorted(set(names + [r.full for r in w.referees]))


def place_words() -> list[str]:
    w = world()
    words: set[str] = set()
    for c in w.clubs:
        words |= {c.name, c.stadium}
        words |= {
            x
            for x in c.stadium.split()
            if x not in ("The", "Arena", "Park", "Ground", "Lane", "Hill", "Green")
        }
    return sorted(words)


def run_checks() -> dict:
    people, places = person_names(), place_words()
    hits: list[Hit] = []
    for i in range(0, len(people), BATCH):
        for b in sparql(people_query(people[i : i + BATCH])):
            hits.append(
                Hit(b["label"]["value"], "person", b["item"]["value"], b.get("occs", {}).get("value", ""))
            )
        time.sleep(PAUSE_S)
    for i in range(0, len(places), BATCH):
        for b in sparql(places_query(places[i : i + BATCH])):
            hits.append(
                Hit(
                    b["label"]["value"],
                    "place",
                    b["item"]["value"],
                    b.get("countryLabel", {}).get("value", ""),
                )
            )
        time.sleep(PAUSE_S)
    return {
        "checked_on": time.strftime("%Y-%m-%d"),
        "source": "Wikidata SPARQL (query.wikidata.org), live; used as a filter only",
        "people_checked": len(people),
        "place_words_checked": len(places),
        "hits": [h.__dict__ for h in sorted(hits, key=lambda h: (h.kind, h.name, h.wikidata))],
        "limits": [
            "GeoNames cities list not used: it needs a file download (cities15000.zip) not yet approved; "
            "Wikidata human settlements are used for places instead.",
            "Exact English-label matches only: a real person under a different spelling is not caught.",
        ],
    }
