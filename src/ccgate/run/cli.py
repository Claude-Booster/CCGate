"""cli.py — `ccgate run`: ccgate-owned ClaudeSDKClient loop (Track B B0)."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path

from ccgate.run.policy import load_contextignore, make_read_deny_hook
from ccgate.run.record import RunRecorder, make_run_id


def _build_options(patterns, enforce: bool):
    """Dict-like config the client_factory consumes. NO SDK import here — keeps run_task
    and the in-process fake-client tests SDK-free. PreToolUse holds the RAW async callback;
    the real _factory wraps it in a HookMatcher (spec §7)."""
    hooks_list = []
    if enforce:
        hooks_list = [make_read_deny_hook(patterns)]   # raw callback; wrapped for the real SDK in _factory
    return {
        "hooks": {"PreToolUse": hooks_list},
        "setting_sources": [],
        # B0 grants only Read. `tools` controls AVAILABILITY (the model can call nothing else),
        # `allowed_tools` auto-APPROVES it (executes without a permission prompt — required in
        # non-interactive mode). Both are needed: allowed_tools alone leaves Bash/Grep available
        # but unapproved, so the model reaches for them and is blocked before ever using Read.
        # Withholding non-Read tools also removes the bypass around the .contextignore deny.
        # B1 broadens tools and extends enforcement (Bash rewrite) to match.
        "tools": ["Read"],
        "allowed_tools": ["Read"],
    }


def _factory(options):
    """Real-SDK client factory: translate the raw-callback dict → ClaudeAgentOptions/HookMatcher.
    All SDK imports are confined here so run_task/_build_options stay SDK-free and testable."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher
    raw = options["hooks"]["PreToolUse"]
    hooks = {"PreToolUse": [HookMatcher(matcher="Read", hooks=raw)]} if raw else {}
    return ClaudeSDKClient(options=ClaudeAgentOptions(
        hooks=hooks,
        setting_sources=options["setting_sources"],
        tools=options["tools"],
        allowed_tools=options["allowed_tools"],
    ))


async def run_task(task_prompt: str, *, enforce: bool, cwd: Path, client_factory,
                   options_builder=_build_options) -> Path:
    patterns = load_contextignore(cwd)
    options = options_builder(patterns, enforce)
    recorder = RunRecorder(make_run_id(str(cwd)))
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
