"""Freeze the current tool surface, or check it for drift.

    {python} scripts/freeze_schema.py            # write config/schema-manifest.json
    {python} scripts/freeze_schema.py --check    # compare, exit 1 on drift

Why a separate script instead of "the gateway writes its own baseline": a
baseline that updates itself cannot detect anything. Freezing is a decision --
"I accept that this is the contract my traces were written against" -- so it
needs a commit, a diff and a person, not a side effect of starting up.

``--check`` is the CI form: it answers "has any upstream changed its contract
since the last freeze" without writing anything.
"""

from __future__ import annotations

import argparse
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import ClientSession  # noqa: F401,E402  (ensures the SDK is importable)

from gateway.config import GatewayConfig, load_config  # noqa: E402
from gateway.registry import (  # noqa: E402
    Registry,
    build_registry,
    load_baseline,
    write_baseline,
)
from gateway.upstream import StdioUpstream  # noqa: E402


async def collect(config: GatewayConfig, *, baseline: Optional[Dict[str, str]] = None):
    upstreams = [
        StdioUpstream(
            spec.name,
            spec.command,
            spec.args,
            env=spec.env,
            cwd=spec.cwd,
            call_timeout_s=spec.call_timeout_s,
        )
        for spec in config.upstreams
    ]
    async with AsyncExitStack() as stack:
        for upstream in upstreams:
            await stack.enter_async_context(upstream)
        listing = [
            (upstream.name, await upstream.list_tools()) for upstream in upstreams
        ]
        return build_registry(listing, baseline=baseline)


def report(registry: Registry) -> None:
    width = max((len(e.exposed_name) for e in registry.entries), default=8)
    width = min(max(width, 24), 68)
    print(f"{'tool':<{width}}  {'upstream':<8} {'hash':<12} state")
    print("-" * (width + 40))
    for entry in registry.entries:
        state = "FROZEN" if entry.frozen else "offered"
        if entry.notes:
            state += f"  notes={len(entry.notes)}"
        print(
            f"{entry.exposed_name:<{width}}  {entry.upstream:<8} "
            f"{entry.schema_hash[:12]} {state}"
        )
    print("-" * (width + 40))
    print(
        f"{len(registry.entries)} tools: {len(registry.offered)} offered, "
        f"{len(registry.frozen)} frozen"
    )

    annotated = [e for e in registry.entries if e.notes]
    if annotated:
        print()
        print("normalization notes (what the gateway changed, and why it matters):")
        for entry in annotated:
            joined = ", ".join(entry.notes)
            print(f"  {entry.exposed_name}")
            print(f"      {joined}")

    if registry.warnings():
        print()
        print("baseline warnings:")
        for note in registry.warnings():
            print(f"  {note}")


def main(argv: Optional[List[str]] = None) -> int:
    import anyio

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--check", action="store_true", help="do not write; exit 1 on drift")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    manifest_path: Path = config.schema_manifest
    baseline = load_baseline(manifest_path)

    registry = anyio.run(collect, config)
    report(registry)

    print()
    if args.check:
        if baseline is None:
            print(f"NO BASELINE at {manifest_path.relative_to(ROOT)} -- run without --check")
            return 1
        drift = [
            e.exposed_name for e in registry.entries if e.schema_hash != baseline.get(e.exposed_name)
        ]
        if drift or registry.warnings():
            print(f"DRIFT: {manifest_path.relative_to(ROOT)} is stale")
            return 1
        print(f"OK: {len(registry.entries)} tool hashes match {manifest_path.relative_to(ROOT)}")
        return 0

    previous = baseline or {}
    write_baseline(manifest_path, registry)
    changed = [
        name
        for name, digest in registry.manifest()["tools"].items()
        if previous.get(name) != digest
    ]
    print(
        f"wrote {manifest_path.relative_to(ROOT)}: {len(registry.entries)} tools, "
        f"{len(changed)} new or changed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
