"""cli.py — `ccgate run`: ccgate-owned ClaudeSDKClient loop (Track B B0)."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path

from ccgate.config import load_config
from ccgate.run.bashcap import BashCapHook, compile_prefixes
from ccgate.run.policy import load_contextignore, make_read_deny_hook
from ccgate.run.record import RunRecorder, make_run_id


def _build_options(patterns, enforce: bool, config: dict, bashcap_hook):
    """Dict-like config the client_factory consumes. NO SDK import here — keeps run_task
    and the in-process fake-client tests SDK-free. PreToolUse holds the RAW F1 callback and
    PostToolUse the RAW F3 callback; the real _factory wraps each in a HookMatcher (spec §7).

    B0 grants only Read (availability + auto-approve). B1b adds Bash — availability AND
    auto-approve — only when F3 is active, so F3 can see Bash output to truncate it."""
    pre = [make_read_deny_hook(patterns)] if enforce else []
    post = [bashcap_hook] if (enforce and bashcap_hook is not None) else []
    tools = ["Read"] + (["Bash"] if post else [])
    return {
        "hooks": {"PreToolUse": pre, "PostToolUse": post},
        "setting_sources": [],
        "tools": tools,
        "allowed_tools": list(tools),
    }


def _factory(options):
    """Real-SDK client factory: translate the raw-callback dict → ClaudeAgentOptions/HookMatcher.
    All SDK imports are confined here so run_task/_build_options stay SDK-free and testable."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher
    pre = options["hooks"]["PreToolUse"]
    post = options["hooks"]["PostToolUse"]
    hooks = {}
    if pre:
        hooks["PreToolUse"] = [HookMatcher(matcher="Read", hooks=pre)]
    if post:
        hooks["PostToolUse"] = [HookMatcher(matcher="Bash", hooks=post)]
    return ClaudeSDKClient(options=ClaudeAgentOptions(
        hooks=hooks,
        setting_sources=options["setting_sources"],
        tools=options["tools"],
        allowed_tools=options["allowed_tools"],
    ))


async def run_task(task_prompt: str, *, enforce: bool, cwd: Path, client_factory,
                   config: dict | None = None) -> Path:
    if config is None:
        config = load_config(str(cwd))
    patterns = load_contextignore(cwd)
    recorder = RunRecorder(make_run_id(str(cwd)))
    bashcap_hook = None
    if enforce and config.get("bashCapEnabled"):
        bashcap_hook = BashCapHook(
            compile_prefixes(config["bashCapPrefixes"]),
            config["bashCapHeadChars"], config["bashCapTailChars"],
            config["bashCapDebugLoopCalls"], recorder)
    options = _build_options(patterns, enforce, config, bashcap_hook)
    async with client_factory(options=options) as client:
        await client.query(task_prompt)
        async for msg in client.receive_response():
            # Stream the assistant's text blocks to stdout (spec §3 streaming; the probe
            # detects whether the read happened by the echoed content appearing here).
            for block in (getattr(msg, "content", None) or []):
                text = getattr(block, "text", None)
                if text:
                    print(text)
            model = getattr(msg, "model", None)
            usage = getattr(msg, "usage", None)
            if model is not None and usage is not None:
                recorder.append_assistant(model, usage)
    if bashcap_hook is not None:
        recorder.append_event(bashcap_hook.summary())   # F3 run summary (spec §6)
    recorder.finish()
    return recorder.path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ccgate run")
    parser.add_argument("--task", required=True, type=Path, help="File containing the task prompt")
    parser.add_argument("--no-enforce", action="store_true",
                        help="MEASUREMENT BASELINE ONLY: omit enforcement — the agent will "
                             "genuinely perform reads that enforcement would deny. Not a safe default.")
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
                                client_factory=_factory))
    print(f"run record: {path}")
