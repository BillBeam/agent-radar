"""agent-radar CLI — the single entry point.

    python -m radar --mode daily|weekly|validate|doctor|status|query|eval|evolve

launchd, the optional /agent-radar skill, and manual runs all call this.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from importlib import import_module
from pathlib import Path

MODES = ["daily", "weekly", "validate", "doctor", "status", "query", "eval", "evolve"]


def cmd_doctor() -> int:
    """Self-diagnostics: is everything wired to run unattended?"""
    from .core.config import Paths, load_config

    ok = True

    def check(label: str, passed: bool, detail: str = "", warn: bool = False) -> None:
        nonlocal ok
        mark = "✓" if passed else ("⚠" if warn else "✗")
        if not passed and not warn:
            ok = False
        print(f"  {mark} {label}{(' — ' + detail) if detail else ''}")

    print("agent-radar doctor\n")

    check("python >= 3.11", sys.version_info >= (3, 11), detail=sys.version.split()[0])

    for dep in ("pydantic", "yaml", "feedparser", "requests"):
        try:
            import_module(dep)
            check(f"dep: {dep}", True)
        except ImportError:
            check(f"dep: {dep}", False, "pip install -e .")

    claude = shutil.which("claude")
    check("claude CLI on PATH", claude is not None, detail=claude or "brew install claude")

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    check("ANTHROPIC_API_KEY unset (use subscription)", not has_key,
          detail="set → would bill API!" if has_key else "good", warn=has_key)

    try:
        cfg = load_config()
        check("config valid", True,
              detail=f"daily≤{cfg.daily_max_items}, threshold={cfg.relevance_threshold}")
        dt = cfg.channels.dingtalk
        check("DingTalk push configured", dt is not None,
              detail="webhook set" if dt else "local+notify only (paste webhook to enable)",
              warn=dt is None)
    except Exception as e:  # noqa: BLE001
        check("config valid", False, repr(e))

    for d in (Paths.data, Paths.candidates, Paths.digests, Paths.trace, Paths.state):
        d.mkdir(parents=True, exist_ok=True)
        check(f"writable: {d.relative_to(Paths.root)}", os.access(d, os.W_OK))

    sources = Paths.sources_yaml
    if sources.exists():
        try:
            import yaml
            data = yaml.safe_load(sources.read_text(encoding="utf-8")) or {}
            n = sum(len(v) for v in data.get("sources", {}).values()) if isinstance(
                data.get("sources"), dict) else len(data.get("sources", []))
            check("sources.yaml parses", True, detail=f"{n} sources")
        except Exception as e:  # noqa: BLE001
            check("sources.yaml parses", False, repr(e))
    else:
        check("sources.yaml present", False, "not created yet (P0 task #2)", warn=True)

    print("\n" + ("all good ✓" if ok else "issues found ✗"))
    return 0 if ok else 1


def cmd_status() -> int:
    from .core.config import Paths
    from .core.io import read_json
    last = read_json(Paths.state / "last_run.json")
    if not last:
        print("no runs yet.")
        return 0
    print("last run:")
    for k, v in last.items():
        print(f"  {k}: {v}")
    return 0


def cmd_validate() -> int:
    try:
        validate = import_module("radar.sources").validate_sources
    except (ModuleNotFoundError, AttributeError):
        print("source validation not implemented yet (P0 task #2).")
        return 1
    return validate()


def cmd_run(mode: str, dry_run: bool) -> int:
    from .core.runner import run_mode
    ctx = run_mode(mode)
    if ctx.digest and ctx.digest.markdown:
        print("\n" + "=" * 60 + f"\n digest: {ctx.digest.date} ({mode})\n" + "=" * 60)
        print(ctx.digest.markdown)
    return 0


def cmd_stub(mode: str) -> int:
    print(f"--mode {mode}: not implemented yet (lands in a later phase).")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="radar", description="agent-radar")
    p.add_argument("--mode", default="daily", choices=MODES)
    p.add_argument("--dry-run", action="store_true", help="fetch+triage but don't deliver")
    p.add_argument("--query", default=None, help="for --mode query")
    args = p.parse_args(argv)

    if args.mode == "doctor":
        return cmd_doctor()
    if args.mode == "status":
        return cmd_status()
    if args.mode == "validate":
        return cmd_validate()
    if args.mode in ("daily", "weekly"):
        return cmd_run(args.mode, args.dry_run)
    return cmd_stub(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
