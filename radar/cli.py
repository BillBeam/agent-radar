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
        check("sources.yaml present", False, "not created yet", warn=True)

    # --- real reachability through the resolved proxy (sources are mostly Western) ---
    import time as _time

    import requests as _requests
    try:
        cfg = load_config()
        proxies, trust_env = cfg.proxy_settings()
        if proxies:
            proxy_desc = f"explicit {cfg.http_proxy}"
        elif trust_env:
            env_p = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            proxy_desc = f"env {env_p}" if env_p else "direct (no env proxy set)"
        else:
            proxy_desc = "direct (env proxy disabled)"
        check("proxy resolved", True, detail=proxy_desc)

        probes = {
            "openai": "https://openai.com/news/rss.xml",
            "huggingface": "https://huggingface.co/api/daily_papers",
            "github": "https://github.com/anthropics/claude-code/releases.atom",
            "arxiv": "http://export.arxiv.org/api/query?search_query=cat:cs.AI&max_results=1",
        }
        sess = _requests.Session()
        sess.trust_env = trust_env
        reachable = 0
        for name, url in probes.items():
            t0 = _time.monotonic()
            try:
                r = sess.get(url, proxies=proxies, timeout=10,
                             headers={"User-Agent": "agent-radar/doctor"})
                r.raise_for_status()
                check(f"reach: {name}", True, detail=f"{(_time.monotonic() - t0) * 1000:.0f}ms")
                reachable += 1
            except Exception as e:  # noqa: BLE001
                check(f"reach: {name}", False, f"{type(e).__name__}: {str(e)[:50]}")
        if reachable == 0:
            check("connectivity", False,
                  "ALL probes failed — set a proxy in config.toml (sources are mostly Western)")
        elif reachable < len(probes):
            none_proxy = not proxies and not trust_env
            check("connectivity", not none_proxy,
                  f"{reachable}/{len(probes)} reachable"
                  + ("; no proxy set — consider one" if none_proxy else ""),
                  warn=not none_proxy)
    except Exception as e:  # noqa: BLE001
        check("reachability", False, repr(e))

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
