"""Import guards (approved plan P1, CEO-3 MCP note, eng ET9).

1. Only ``earshot.sim`` and ``earshot.eval`` may import ``earshot.hidden`` (the truth log).
2. Nothing that serves listeners or models (agents, tools, api, stats, contract, world) may reach
   the truth-producing simulator modules, even transitively. They may use the pure helpers
   ``earshot.sim.stoppages`` (frozen dead-time parameters), ``earshot.sim.io`` and
   ``earshot.sim.ballpath``.
3. The MCP entrypoint ``earshot.api.mcp`` imports only tools, stats, contract and pitch code.

The scan is static (AST), so it also covers imports inside functions.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
PKG = SRC / "earshot"
SIM_ALLOWED = {"earshot.sim", "earshot.sim.stoppages", "earshot.sim.io", "earshot.sim.ballpath"}
CONSUMERS = (
    "earshot.agents",
    "earshot.live",
    "earshot.tools",
    "earshot.api",
    "earshot.stats",
    "earshot.contract",
    "earshot.world",
    "earshot.pipeline",
)


def module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@cache
def modules() -> dict[str, Path]:
    return {module_name(p): p for p in PKG.rglob("*.py")}


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return {m for m in out if m == "earshot" or m.startswith("earshot.")}


def resolve(name: str) -> str | None:
    """The module a dotted import refers to (``from earshot.x import y`` may name a symbol)."""
    mods = modules()
    while name:
        if name in mods:
            return name
        name = name.rpartition(".")[0]
    return None


@cache
def closure(name: str) -> frozenset[str]:
    seen: set[str] = set()
    stack = [name]
    while stack:
        m = stack.pop()
        if m in seen or m not in modules():
            continue
        seen.add(m)
        for imp in imports_of(modules()[m]):
            r = resolve(imp)
            if r and r not in seen:
                stack.append(r)
            # Importing a submodule also runs its parent packages.
            parent = r.rpartition(".")[0] if r else ""
            while parent:
                if parent not in seen:
                    stack.append(parent)
                parent = parent.rpartition(".")[0]
    return frozenset(seen)


def in_package(mod: str, pkg: str) -> bool:
    return mod == pkg or mod.startswith(pkg + ".")


def test_scanner_sees_the_package():
    assert "earshot.hidden" in modules()
    assert "earshot.sim.match" in modules()
    assert "earshot.hidden" in closure("earshot.sim.runner")


@pytest.mark.parametrize("mod", sorted(modules()))
def test_only_sim_and_eval_import_hidden(mod):
    if in_package(mod, "earshot.hidden") or in_package(mod, "earshot.sim") or in_package(mod, "earshot.eval"):
        return
    direct = {resolve(i) for i in imports_of(modules()[mod])}
    assert not any(d and in_package(d, "earshot.hidden") for d in direct), f"{mod} imports earshot.hidden"


@pytest.mark.parametrize("mod", sorted(m for m in modules() if any(in_package(m, c) for c in CONSUMERS)))
def test_consumers_never_reach_truth(mod):
    reach = closure(mod)
    bad = sorted(
        m
        for m in reach
        if in_package(m, "earshot.hidden")
        or in_package(m, "earshot.eval")
        or (in_package(m, "earshot.sim") and m not in SIM_ALLOWED)
    )
    assert not bad, f"{mod} reaches {bad}"


def test_mcp_entrypoint_imports_only_tools_stats_contract():
    if "earshot.api.mcp" not in modules():
        pytest.skip("MCP app not built yet")
    allowed = ("earshot.tools", "earshot.stats", "earshot.contract", "earshot.pitch", "earshot.api")
    reach = closure("earshot.api.mcp") - {"earshot"}
    bad = sorted(m for m in reach if not any(in_package(m, a) for a in allowed))
    assert not bad, f"earshot.api.mcp reaches {bad}"


def test_guard_catches_a_forbidden_import(tmp_path, monkeypatch):
    """The guard itself works: a planted import of earshot.hidden is found."""
    planted = tmp_path / "planted.py"
    planted.write_text("from earshot.hidden import read_truth\n")
    assert any(resolve(i) == "earshot.hidden" for i in imports_of(planted))
