"""No call in the tree passes the wrong number of arguments.

Python does not check this, the import contracts are about something else, and
§13 in docs/found-during-move.md is what it costs: `_do_refresh_orders` was
called with three arguments and declared four, so every order refresh raised
TypeError before reaching the network and did so in production for a day. It was
found by reading, which is not a strategy.

Deliberately conservative — it reports only certain mismatches. Calls through an
attribute, calls with `*args` or `**kwargs`, and functions passed around as
values are all skipped, because guessing at those would produce noise and a
noisy check gets deleted. What it does cover is the exact shape that broke:
a plain call to a function defined here or imported from a sibling module.
"""
from __future__ import annotations

import ast
import pathlib

from tests.conftest import REPO_ROOT

PACKAGES = ("bot", "core")


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    args = node.args
    positional = args.posonlyargs + args.args
    return {
        "min": len(positional) - len(args.defaults),
        "max": None if args.vararg else len(positional),
        "keywords": {a.arg for a in positional} | {a.arg for a in args.kwonlyargs},
        "kwargs": args.kwarg is not None,
    }


def _module_name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(REPO_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _trees() -> dict[str, tuple[pathlib.Path, ast.Module]]:
    out = {}
    for path in sorted(pathlib.Path(REPO_ROOT).glob("**/*.py")):
        parts = path.relative_to(REPO_ROOT).parts
        if parts[0] not in PACKAGES or "__pycache__" in parts:
            continue
        out[_module_name(path)] = (path, ast.parse(path.read_text(), filename=str(path)))
    return out


def test_no_call_disagrees_with_its_definition():
    trees = _trees()
    defined = {
        module: {
            node.name: _signature(node)
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for module, (_path, tree) in trees.items()
    }

    problems: list[str] = []
    checked = 0

    for module, (path, tree) in trees.items():
        # What a bare name in this module can refer to: its own functions, plus
        # whatever it imported from another module of ours.
        visible = dict(defined[module])
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and not node.level:
                for alias in node.names:
                    target = defined.get(node.module, {}).get(alias.name)
                    if target:
                        visible[alias.asname or alias.name] = target

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            target = visible.get(node.func.id)
            if target is None:
                continue
            if any(isinstance(a, ast.Starred) for a in node.args):
                continue
            if any(k.arg is None for k in node.keywords):
                continue

            checked += 1
            given = len(node.args)
            keywords = {k.arg for k in node.keywords}
            where = f"{path.relative_to(REPO_ROOT)}:{node.lineno} {node.func.id}()"

            if target["max"] is not None and given > target["max"]:
                problems.append(
                    f"{where} — {given} positional arguments, takes at most {target['max']}")
            elif given + len(keywords & target["keywords"]) < target["min"]:
                problems.append(
                    f"{where} — {given + len(keywords & target['keywords'])} of "
                    f"{target['min']} required parameters covered")
            elif keywords - target["keywords"] and not target["kwargs"]:
                problems.append(
                    f"{where} — unknown arguments: {sorted(keywords - target['keywords'])}")

    assert not problems, "\n".join(problems)
    # A check that silently stops checking is worse than no check: this is the
    # tripwire for the day an import rewrite makes every call unresolvable.
    assert checked > 300, f"only {checked} calls resolved — the scan stopped seeing the tree"
