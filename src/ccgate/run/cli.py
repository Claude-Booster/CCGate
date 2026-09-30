"""cli.py — `ccgate run`: ccgate-owned ClaudeSDKClient loop (Track B B0)."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path

from ccgate.config import load_config
from ccgate.run.policy import load_contextignore
from ccgate.run.record import RunRecorder, make_run_id


def _build_options(patterns, enforce: bool, config: dict, recorder, max_turns=None):
    """Dict-like config the client_factory consumes. NO SDK import here — keeps run_task
    and the in-process fake-client tests SDK-free. PreToolUse/PostToolUse hold (matcher, raw
    callback) tuples; the real _factory wraps each in a HookMatcher (spec §7).

    B0 grants only Read. When bashEnabled (spec §4), Bash is granted and BOTH the F1 Bash-read
    deny (PreToolUse) and F3 truncation (PostToolUse) register — each self-gates on its prefix
    list. `tools` is kept minimal: under the model, only listed tools are available."""
    from ccgate.run.policy import make_read_deny_hook, make_bash_read_deny_hook
    from ccgate.run.bashcap import BashCapHook
    from ccgate.run.shellcmd import compile_prefixes
    # Tool AVAILABILITY is independent of enforce: --no-enforce is the measurement baseline and
    # must offer the SAME tools as the enforced run (so it exercises the same task) — only the
    # deny/truncation HOOKS are gated by enforce. Gating the Read grant on enforce would leave
    # --no-enforce with empty tools, and the baseline could not read at all.
    bash_on = bool(config.get("bashEnabled"))
    tools = ["Read"] + (["Bash"] if bash_on else [])
    pre, post = [], []
    if enforce:
        pre.append(("Read", make_read_deny_hook(patterns, recorder)))
        if bash_on:
            readers = compile_prefixes(config["bashReadPrefixes"])
            pre.append(("Bash", make_bash_read_deny_hook(patterns, readers, recorder)))
            post.append(("Bash", BashCapHook(
                compile_prefixes(config["bashCapPrefixes"]),
                config["bashCapHeadChars"], config["bashCapTailChars"],
                config["bashCapDebugLoopCalls"], recorder)))
    return {
        "hooks": {"PreToolUse": pre, "PostToolUse": post},
        "setting_sources": [],
        "tools": tools,
        "allowed_tools": list(tools),
        "max_turns": max_turns,
    }


def _factory(options):
    """Real-SDK client factory: translate the raw-callback dict → ClaudeAgentOptions/HookMatcher.
    One HookMatcher per (matcher, callback); multiple matchers on one event are supported and
    run concurrently in the CLI (spec §2). All SDK imports are confined here."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher
    hooks = {}
    pre = [HookMatcher(matcher=m, hooks=[cb]) for m, cb in options["hooks"]["PreToolUse"]]
    post = [HookMatcher(matcher=m, hooks=[cb]) for m, cb in options["hooks"]["PostToolUse"]]
    if pre:
        hooks["PreToolUse"] = pre
    if post:
        hooks["PostToolUse"] = post
    return ClaudeSDKClient(options=ClaudeAgentOptions(
        hooks=hooks,
        setting_sources=options["setting_sources"],
        tools=options["tools"],
        allowed_tools=options["allowed_tools"],
        max_turns=options["max_turns"],
    ))


async def run_task(task_prompt: str, *, enforce: bool, cwd: Path, client_factory,
                   config: dict | None = None, max_turns: int | None = None) -> Path:
    if config is None:
        config = load_config(str(cwd))
    patterns = load_contextignore(cwd)
    recorder = RunRecorder(make_run_id(str(cwd)))
    options = _build_options(patterns, enforce, config, recorder, max_turns)
    # Stateful hooks (bash-read deny, bashcap) expose summary(); the read-deny closure does not.
    summaries = [cb for _, cb in options["hooks"]["PostToolUse"]]
    summaries += [cb for _, cb in options["hooks"]["PreToolUse"] if hasattr(cb, "summary")]
    async with client_factory(options=options) as client:
        await client.query(task_prompt)
        async for msg in client.receive_response():
            # Stream the assistant's text blocks to stdout (spec §3 streaming).
            for block in (getattr(msg, "content", None) or []):
                text = getattr(block, "text", None)
                if text:
                    print(text)
            model = getattr(msg, "model", None)
            usage = getattr(msg, "usage", None)
            if model is not None and usage is not None:
                recorder.append_assistant(model, usage)
    for h in summaries:
        recorder.append_event(h.summary())   # F1/F3 run summaries (spec §6)
    recorder.finish()
    return recorder.path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ccgate run")
    parser.add_argument("--task", required=True, type=Path, help="File containing the task prompt")
    parser.add_argument("--no-enforce", action="store_true",
                        help="MEASUREMENT BASELINE ONLY: omit enforcement — the agent will "
                             "genuinely perform reads that enforcement would deny. Not a safe default.")
    parser.add_argument("--max-turns", type=int, default=None,
                        help="Per-run turn cap (measurement cost control). Omit for no cap.")
    args = parser.parse_args(argv)
    if not args.task.exists():
        print(f"ccgate run: task file not found: {args.task}", file=sys.stderr)
        sys.exit(1)
    try:
        prompt = args.task.read_text(encoding="utf-8")
    except OSError as e:
        print(f"ccgate run: cannot read task file {args.task}: {e}", file=sys.stderr)
        sys.exit(1)
    if importlib.util.find_spec("claude_agent_sdk") is None:
        print("ccgate run requires the 'run' extra: pip install -e '.[run]'", file=sys.stderr)
        sys.exit(1)
    path = asyncio.run(run_task(prompt, enforce=not args.no_enforce, cwd=Path.cwd(),
                                client_factory=_factory, max_turns=args.max_turns))
    print(f"run record: {path}")
