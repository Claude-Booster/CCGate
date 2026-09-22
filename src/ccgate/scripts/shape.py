"""shape.py — static config lint (Phase 0). Checks: G2 (claudeMdLines), G3 (skillListing)."""
import json
import os
import datetime
import subprocess
import sys
from pathlib import Path

from ccgate.state import ccgate_home

_CHARS_PER_TOKEN = 4
_CLAUDEMD_MAX_LINES = 200


def _check_claudemd_lines(paths: list[Path]) -> list[dict]:
    """G2: each loaded CLAUDE.md must be ≤ 200 lines."""
    findings = []
    for path in paths:
        try:
            lines = len(path.read_text(encoding="utf-8").splitlines())
        except OSError:
            continue
        if lines > _CLAUDEMD_MAX_LINES:
            findings.append({
                "check": "claudeMdLines",
                "severity": "error",
                "message": (
                    f"{path} has {lines} lines (limit {_CLAUDEMD_MAX_LINES}). "
                    "Run /doctor for trim suggestions."
                ),
            })
    return findings


def _find_claudemd_files(cwd: str | None) -> list[Path]:
    """Return CLAUDE.md files that Claude Code would load for a given cwd."""
    candidates: list[Path] = []
    global_path = Path.home() / ".claude" / "CLAUDE.md"
    if global_path.exists():
        candidates.append(global_path)
    if cwd:
        project = Path(cwd)
        for parent in [project, *project.parents]:
            for name in ("CLAUDE.md", ".claude/CLAUDE.md"):
                p = parent / name
                if p.exists():
                    candidates.append(p)
            if parent == Path(parent.anchor):
                break
    return candidates


def _check_skill_listing(
    total_desc_chars: int,
    window_tokens: int,
    budget_fraction: float,
) -> list[dict]:
    """G3: predicted description chars must be ≤ skillListingBudgetFraction × window chars."""
    budget_chars = budget_fraction * window_tokens * _CHARS_PER_TOKEN
    if total_desc_chars > budget_chars:
        return [{
            "check": "skillListing",
            "severity": "warning",
            "message": (
                f"Skill descriptions total ~{total_desc_chars} chars; "
                f"budget is {budget_chars:.0f} chars "
                f"({budget_fraction*100:.1f}% of {window_tokens:,}-token window). "
                "Run /skill-doctor to identify large descriptions."
            ),
        }]
    return []


def _measure_skill_descriptions(cwd: str | None) -> int:
    """Sum character lengths of all skill description fields in loaded SKILL.md files."""
    search_roots = [Path.home() / ".claude"]
    if cwd:
        search_roots.append(Path(cwd) / ".claude")
    total = 0
    for root in search_roots:
        for skill_file in root.rglob("SKILL.md"):
            try:
                content = skill_file.read_text(encoding="utf-8")
            except OSError:
                continue
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    frontmatter = content[3:end]
                    for line in frontmatter.splitlines():
                        if line.strip().startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip("\"'")
                            total += len(desc)
    return total


_SIDE_EFFECT_KEYWORDS = {"deploy", "commit", "publish", "send"}


def _check_skill_side_effects(cwd: str | None) -> list[dict]:
    """skillSideEffects: skills named deploy/commit/publish/send without disable-model-invocation."""
    search_roots = [Path.home() / ".claude"]
    if cwd:
        search_roots.append(Path(cwd) / ".claude")
    findings = []
    for root in search_roots:
        if not root.exists():
            continue
        for skill_file in root.rglob("SKILL.md"):
            try:
                content = skill_file.read_text(encoding="utf-8")
            except OSError:
                continue
            if not content.startswith("---"):
                continue
            end = content.find("---", 3)
            if end == -1:
                continue
            frontmatter = content[3:end]
            name = ""
            disable_model = False
            for line in frontmatter.splitlines():
                stripped = line.strip()
                if stripped.startswith("name:"):
                    name = stripped.split(":", 1)[1].strip().strip("\"'")
                if stripped.startswith("disable-model-invocation:"):
                    val = stripped.split(":", 1)[1].strip().lower()
                    disable_model = val == "true"
            if any(kw in name.lower() for kw in _SIDE_EFFECT_KEYWORDS) and not disable_model:
                findings.append({
                    "check": "skillSideEffects",
                    "severity": "error",
                    "message": (
                        f"{skill_file}: skill '{name}' matches a side-effect keyword "
                        "(deploy/commit/publish/send) but is missing "
                        "'disable-model-invocation: true'. Add it to the skill's frontmatter."
                    ),
                })
    return findings


def _check_claudemd_excludes(cwd: str | None) -> list[dict]:
    """claudeMdExcludes: monorepo with >1 package but no package-level CLAUDE.md files."""
    if not cwd:
        return []
    project = Path(cwd)
    manifests = []
    for pattern in ("pyproject.toml", "package.json"):
        for p in project.rglob(pattern):
            if ".git" not in p.parts and "node_modules" not in p.parts:
                manifests.append(p)
    if len(manifests) <= 1:
        return []
    # Check if any sub-package has a scoped CLAUDE.md
    sub_manifests = [p for p in manifests if p.parent != project]
    has_scoped = any(
        (p.parent / "CLAUDE.md").exists() or (p.parent / ".claude" / "CLAUDE.md").exists()
        for p in sub_manifests
    )
    if has_scoped:
        return []
    return [{
        "check": "claudeMdExcludes",
        "severity": "warning",
        "message": (
            f"Monorepo with {len(manifests)} package manifest(s) detected but no "
            "package-level CLAUDE.md files found. Add a CLAUDE.md in each package "
            "directory so Claude Code loads only relevant context per package."
        ),
    }]


_SENSITIVE_DIRS = ["dist", "build", "vendor"]


def _load_settings(cwd: str | None) -> dict:
    """Load merged Claude Code settings.json (global then project-level)."""
    import json as _json
    settings: dict = {}
    candidates = [Path.home() / ".claude" / "settings.json"]
    if cwd:
        candidates.append(Path(cwd) / ".claude" / "settings.json")
    for p in candidates:
        if p.exists():
            try:
                settings.update(_json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    return settings


_CC_VERSION_FLOOR_TTL_1H    = (2, 1, 108)   # ENABLE_PROMPT_CACHING_1H
_CC_VERSION_FLOOR_SUBAGENT  = (2, 1, 257)   # CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL


def _get_cc_version() -> tuple[int, int, int] | None:
    """Return Claude Code version as a 3-tuple, or None if undetectable."""
    import re as _re
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        m = _re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout or result.stderr or "")
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        pass
    return None


def _is_subscription_auth(settings: dict) -> bool:
    """Return True when the session uses subscription auth (1h TTL already managed).

    Subscription auth has no apiKeyHelper or cloudProviderId; and the most
    recent statusline snapshot reports prompt_cache.ttl == "1h".
    """
    if "apiKeyHelper" in settings or "cloudProviderId" in settings:
        return False
    sessions_dir = ccgate_home() / "sessions"
    if not sessions_dir.exists():
        return False
    snapshots = sorted(
        sessions_dir.glob("*-statusline.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for snap in snapshots[:3]:
        try:
            data = json.loads(snap.read_text(encoding="utf-8"))
            if data.get("prompt_cache", {}).get("ttl") == "1h":
                return True
        except (json.JSONDecodeError, OSError):
            pass
    return False


def _check_session_pinning(settings: dict) -> list[dict]:
    """A1/G18: flag missing or version-gated cache-pinning env keys."""
    if _is_subscription_auth(settings):
        return []

    env_block = settings.get("env", {})
    cc_version = _get_cc_version()
    findings: list[dict] = []

    # ENABLE_PROMPT_CACHING_1H
    if not env_block.get("ENABLE_PROMPT_CACHING_1H"):
        if cc_version is not None and cc_version < _CC_VERSION_FLOOR_TTL_1H:
            findings.append({
                "check": "version_gap",
                "severity": "warning",
                "message": (
                    f"ENABLE_PROMPT_CACHING_1H requires Claude Code ≥ 2.1.108 "
                    f"(detected {'.'.join(str(v) for v in cc_version)}). "
                    "Upgrade Claude Code to enable the 1-hour cache TTL."
                ),
            })
        else:
            findings.append({
                "check": "sessionPinning",
                "severity": "warning",
                "message": (
                    "ENABLE_PROMPT_CACHING_1H not set in settings.json env block. "
                    "Sessions idle > 5 min will miss the cache. "
                    "Run 'ccgate shape --fix' to apply."
                ),
            })

    # CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL
    if not env_block.get("CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"):
        if cc_version is not None and cc_version < _CC_VERSION_FLOOR_SUBAGENT:
            findings.append({
                "check": "version_gap",
                "severity": "warning",
                "message": (
                    f"CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL requires Claude Code ≥ 2.1.257 "
                    f"(detected {'.'.join(str(v) for v in cc_version)}). "
                    "Upgrade Claude Code to extend subagent cache TTL."
                ),
            })
        else:
            findings.append({
                "check": "sessionPinning",
                "severity": "warning",
                "message": (
                    "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL not set. "
                    "Subagent sessions use the 5-minute default. "
                    "Run 'ccgate shape --fix' to apply."
                ),
            })

    return findings


def _stage_session_pinning(settings: dict, fixes: list) -> None:
    """Append A1 fix entries to fixes list. Omits keys below their version floor."""
    if _is_subscription_auth(settings):
        return
    env_block = settings.get("env", {})
    cc_version = _get_cc_version()

    if not env_block.get("ENABLE_PROMPT_CACHING_1H"):
        if cc_version is None or cc_version >= _CC_VERSION_FLOOR_TTL_1H:
            fixes.append({
                "check": "sessionPinning",
                "file": "~/.claude/settings.json",
                "action": "json_set",
                "key": "env.ENABLE_PROMPT_CACHING_1H",
                "value": "1",
                "description": "Pin 1-hour prompt cache TTL (A1)",
            })

    if not env_block.get("CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"):
        if cc_version is None or cc_version >= _CC_VERSION_FLOOR_SUBAGENT:
            fixes.append({
                "check": "sessionPinning",
                "file": "~/.claude/settings.json",
                "action": "json_set",
                "key": "env.CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL",
                "value": "1h",
                "description": "Pin subagent prompt cache TTL to 1 hour (A1)",
            })


def _is_first_party_base_url(url: str) -> bool:
    """Return True if url points to an Anthropic first-party endpoint."""
    return "anthropic.com" in url


def _load_mcp_configs(cwd: str | None) -> list[tuple[str, dict]]:
    """Return [(path_str, parsed_dict)] for each MCP config file found."""
    candidates: list[Path] = [Path.home() / ".claude" / "mcp.json"]
    if cwd:
        candidates.extend([
            Path(cwd) / ".claude" / "mcp.json",
            Path(cwd) / ".mcp.json",
        ])
    result: list[tuple[str, dict]] = []
    seen: set[Path] = set()
    for p in candidates:
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        if resolved in seen:
            continue
        seen.add(resolved)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                result.append((str(p).replace("\\", "/"), data))
            except (json.JSONDecodeError, OSError):
                pass
    return result


def _check_tool_deferral(settings: dict, cwd: str | None) -> list[dict]:
    """A3/G21: flag gateway base URL without ENABLE_TOOL_SEARCH; flag alwaysLoad."""
    findings: list[dict] = []
    env_block = settings.get("env", {})
    base_url = env_block.get("ANTHROPIC_BASE_URL", "")

    if base_url and not _is_first_party_base_url(base_url):
        if not env_block.get("ENABLE_TOOL_SEARCH"):
            findings.append({
                "check": "toolDeferral",
                "severity": "error",
                "message": (
                    f"Non-first-party ANTHROPIC_BASE_URL ({base_url!r}) without "
                    "ENABLE_TOOL_SEARCH=true. MCP tool definitions load upfront "
                    "(~100K tokens), invalidating the cache on every "
                    "connect/disconnect. Run 'ccgate shape --fix' to apply."
                ),
            })

    for path_str, data in _load_mcp_configs(cwd):
        servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
        for name, server in servers.items():
            if isinstance(server, dict) and server.get("alwaysLoad"):
                findings.append({
                    "check": "toolDeferral",
                    "severity": "warning",
                    "message": (
                        f"MCP server {name!r} in {path_str} has 'alwaysLoad: true'. "
                        "Tool definitions will be loaded into the prefix, "
                        "invalidating the cache on connect/disconnect. "
                        "Set alwaysLoad to false or remove it."
                    ),
                })

    return findings


def _stage_tool_deferral(
    settings: dict, cwd: str | None, fixes: list, skipped: list
) -> None:
    """Append A3 fix/skip entries. Gateway URL → fix. alwaysLoad → skipped."""
    env_block = settings.get("env", {})
    base_url = env_block.get("ANTHROPIC_BASE_URL", "")

    if base_url and not _is_first_party_base_url(base_url) and not env_block.get("ENABLE_TOOL_SEARCH"):
        fixes.append({
            "check": "toolDeferral",
            "file": "~/.claude/settings.json",
            "action": "json_set",
            "key": "env.ENABLE_TOOL_SEARCH",
            "value": "true",
            "description": "Enable tool search deferral for non-first-party base URL (A3)",
        })

    for path_str, data in _load_mcp_configs(cwd):
        servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
        for name, server in servers.items():
            if isinstance(server, dict) and server.get("alwaysLoad"):
                skipped.append({
                    "check": "toolDeferral",
                    "reason": (
                        f"Cannot auto-fix alwaysLoad on {name!r} in {path_str} — "
                        "may be deliberate. Remove or set to false manually."
                    ),
                })


def _summarize_tool_deferral(cwd: str | None) -> list[dict]:
    """Emit one info-level summary of MCP server deferral state."""
    configs = _load_mcp_configs(cwd)
    total = 0
    always_loaded = 0
    for _, data in configs:
        servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
        for server in servers.values():
            total += 1
            if isinstance(server, dict) and server.get("alwaysLoad"):
                always_loaded += 1
    if total == 0:
        return []
    deferred = total - always_loaded
    return [{
        "check": "toolDeferralSummary",
        "severity": "info",
        "message": (
            f"MCP servers: {total} total, {deferred} deferred, "
            f"{always_loaded} always-loaded."
        ),
    }]


def _check_cache_ttl(settings: dict) -> list[dict]:
    """cacheTtl: API-key or cloud-provider auth with promptCacheTtl unset."""
    has_api_indicators = "apiKeyHelper" in settings or "cloudProviderId" in settings
    if not has_api_indicators:
        return []
    if settings.get("promptCacheTtl"):
        return []
    return [{
        "check": "cacheTtl",
        "severity": "warning",
        "message": (
            "API-key or cloud-provider auth detected but 'promptCacheTtl' is not set. "
            "The default TTL is 5 minutes. Set \"promptCacheTtl\": \"1h\" in "
            "~/.claude/settings.json to use the 1-hour cache window and avoid "
            "frequent cache misses on idle windows."
        ),
    }]


def _check_deny_reads(cwd: str | None, settings: dict) -> list[dict]:
    """denyReads: flag dist/, build/, vendor/, *.generated.* with no permissions.deny rule."""
    if not cwd:
        return []
    project = Path(cwd)
    deny_rules: list[str] = settings.get("permissions", {}).get("deny", [])
    findings = []

    for dirname in _SENSITIVE_DIRS:
        target = project / dirname
        if not target.exists():
            continue
        covered = any(r.startswith(f"Read({dirname}/") for r in deny_rules)
        if not covered:
            findings.append({
                "check": "denyReads",
                "severity": "warning",
                "message": (
                    f"Directory '{dirname}/' exists but has no matching "
                    f"'Read({dirname}/**/*) deny rule in permissions.deny. "
                    "Add it to .claude/settings.json to prevent unintentional reads."
                ),
            })

    generated = [
        p for p in project.rglob("*.generated.*")
        if ".git" not in p.parts and "node_modules" not in p.parts
    ]
    if generated:
        covered = any("generated" in r for r in deny_rules)
        if not covered:
            findings.append({
                "check": "denyReads",
                "severity": "warning",
                "message": (
                    f"Found {len(generated)} *.generated.* file(s) but no matching deny "
                    "rule. Add 'Read(**/*.generated.*/***)' to permissions.deny."
                ),
            })
    return findings


def _check_worktree_sparse(cwd: str | None, settings: dict) -> list[dict]:
    """worktreeSparse: git worktree in a monorepo without worktree.sparsePaths."""
    if not cwd:
        return []
    project = Path(cwd)
    git_path = project / ".git"
    if not git_path.is_file():
        return []  # .git is a directory in the main checkout; a file only inside worktrees
    manifests = []
    for pattern in ("pyproject.toml", "package.json"):
        for p in project.rglob(pattern):
            if ".git" not in p.parts and "node_modules" not in p.parts:
                manifests.append(p)
    if len(manifests) <= 1:
        return []
    if settings.get("worktree", {}).get("sparsePaths"):
        return []
    return [{
        "check": "worktreeSparse",
        "severity": "warning",
        "message": (
            "Git worktree detected in a monorepo but 'worktree.sparsePaths' is not set. "
            "Configure it in settings.json to limit which packages are visible, "
            "reducing context overhead per session."
        ),
    }]


def _check_output_caps(cwd: str | None) -> list[dict]:
    """outputCaps: check for missing output caps where session history shows large outputs.

    Phase 0 stub — requires state.py session history (Phase 1). Always returns [].
    """
    return []


def _resolve_fix_path(file_str: str, cwd: str | None) -> Path:
    """Resolve a fix file path. Tilde paths resolve via Path.home(); others relative to cwd."""
    if file_str.startswith("~/"):
        return Path.home() / file_str[2:]
    if cwd:
        return Path(cwd) / file_str
    return Path(file_str)


def _safe_json_patch(path: Path, action: str, key: str, value) -> None:
    """json_set or json_append with backup → tmp → atomic replace. Raises on any error."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        original_text = path.read_text(encoding="utf-8")
        path.with_name(path.name + ".ccgate-bak").write_text(original_text, encoding="utf-8")
        data = json.loads(original_text)
    else:
        data = {}
    # Navigate dotted key path: "permissions.deny" → data["permissions"]["deny"]
    parts = key.split(".")
    obj = data
    for part in parts[:-1]:
        obj = obj.setdefault(part, {})
    leaf = parts[-1]
    if action == "set":
        obj[leaf] = value
    elif action == "append":
        arr = obj.setdefault(leaf, [])
        if value not in arr:
            arr.append(value)
    new_text = json.dumps(data, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except PermissionError:
        # Windows: Claude Code holds settings.json with no FILE_SHARE_DELETE,
        # blocking os.replace() (which needs rename/delete on the target).
        # A direct write only needs FILE_SHARE_WRITE, which succeeds.
        tmp.unlink(missing_ok=True)
        path.write_text(new_text, encoding="utf-8")


def _safe_create(path: Path, content: str) -> None:
    """Write content to path only if the file does not already exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _safe_frontmatter_set(path: Path, key: str, value) -> None:
    """Set a key in YAML frontmatter with backup → tmp → atomic replace."""
    if not path.exists():
        return
    original_text = path.read_text(encoding="utf-8")
    path.with_name(path.name + ".ccgate-bak").write_text(original_text, encoding="utf-8")
    if not original_text.startswith("---"):
        return
    end = original_text.find("---", 3)
    if end == -1:
        return
    frontmatter_text = original_text[3:end]
    rest = original_text[end:]
    yaml_val = str(value).lower() if isinstance(value, bool) else str(value)
    key_line = f"{key}: {yaml_val}"
    new_lines = [ln for ln in frontmatter_text.splitlines() if not ln.strip().startswith(f"{key}:")]
    new_lines.append(key_line)
    new_frontmatter = "\n".join(new_lines)
    new_content = f"---{new_frontmatter}\n{rest}"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new_content, encoding="utf-8")
    os.replace(tmp, path)


def stage_fixes(
    cwd: str | None = None,
    config: dict | None = None,
    fix_skills: bool = False,
) -> dict:
    """Run checks and stage proposed fixes. Returns a fix report dict (applied=False).

    Never writes to any settings or project file — only produces the dict.
    """
    if config is None:
        from ccgate.config import DEFAULTS
        config = DEFAULTS

    fixes: list[dict] = []
    skipped: list[dict] = []
    settings = _load_settings(cwd)

    # cacheTtl — fix targets global settings
    if _check_cache_ttl(settings):
        fixes.append({
            "check": "cacheTtl",
            "file": "~/.claude/settings.json",
            "action": "json_set",
            "key": "promptCacheTtl",
            "value": "1h",
            "description": "Set prompt cache TTL to 1 hour",
        })

    # sessionPinning (A1) — fix targets global settings
    if config.get("pinCacheTtl", True):
        _stage_session_pinning(settings, fixes)

    # toolDeferral (A3) — fix targets global settings; alwaysLoad goes to skipped
    if config.get("enforceToolDeferral", True):
        _stage_tool_deferral(settings, cwd, fixes, skipped)

    # denyReads — detect which dirs/patterns need coverage
    if cwd:
        deny_rules: list[str] = settings.get("permissions", {}).get("deny", [])
        project = Path(cwd)
        for dirname in _SENSITIVE_DIRS:
            if (project / dirname).exists():
                if not any(r.startswith(f"Read({dirname}/") for r in deny_rules):
                    fixes.append({
                        "check": "denyReads",
                        "file": ".claude/settings.json",
                        "action": "json_append",
                        "key": "permissions.deny",
                        "value": f"Read({dirname}/**/*)",
                        "description": f"Deny reads of {dirname}/ directory",
                    })
        generated = [
            p for p in project.rglob("*.generated.*")
            if ".git" not in p.parts and "node_modules" not in p.parts
        ]
        if generated and not any("generated" in r for r in deny_rules):
            fixes.append({
                "check": "denyReads",
                "file": ".claude/settings.json",
                "action": "json_append",
                "key": "permissions.deny",
                "value": "Read(**/*.generated.*)",
                "description": "Deny reads of generated files",
            })

    # worktreeSparse — fix targets project settings
    if cwd and _check_worktree_sparse(cwd, settings):
        project = Path(cwd)
        manifests = []
        for pattern in ("pyproject.toml", "package.json"):
            for p in project.rglob(pattern):
                if ".git" not in p.parts and "node_modules" not in p.parts:
                    manifests.append(p)
        sub_dirs = sorted({
            str(p.parent.relative_to(project)).replace("\\", "/")
            for p in manifests if p.parent != project
        })
        fixes.append({
            "check": "worktreeSparse",
            "file": ".claude/settings.json",
            "action": "json_set",
            "key": "worktree.sparsePaths",
            "value": sub_dirs,
            "description": "Set worktree sparse paths for monorepo packages",
        })

    # outputCaps — dormant stub; only emits a fix if the check fires (never in Phase 0)
    if cwd and _check_output_caps(cwd):
        fixes.append({
            "check": "outputCaps",
            "file": "~/.claude/settings.json",
            "action": "json_set",
            "key": "bashOutputMaxChars",
            "value": 30000,
            "description": "Cap bash output at 30,000 chars",
        })

    # claudeMdExcludes — create stub CLAUDE.md per unscoped sub-package
    if cwd and _check_claudemd_excludes(cwd):
        project = Path(cwd)
        manifests = []
        for pattern in ("pyproject.toml", "package.json"):
            for p in project.rglob(pattern):
                if ".git" not in p.parts and "node_modules" not in p.parts:
                    manifests.append(p)
        for manifest in manifests:
            if manifest.parent == project:
                continue
            pkg_name = manifest.parent.name
            claudemd = manifest.parent / "CLAUDE.md"
            rel = str(claudemd.relative_to(project)).replace("\\", "/")
            fixes.append({
                "check": "claudeMdExcludes",
                "file": rel,
                "action": "create",
                "content": f"# {pkg_name}\n",
                "description": f"Create stub CLAUDE.md for package {pkg_name}",
            })

    # skillSideEffects — only staged when --fix-skills is passed
    if fix_skills:
        seen_skill_paths: set[str] = set()
        for finding in _check_skill_side_effects(cwd):
            # message format: "<path>: skill '<name>' ..."
            # Split on ": skill " to avoid splitting on Windows drive-letter colon
            skill_path_str = finding["message"].split(": skill ", 1)[0].strip()
            # Deduplicate: global and cwd roots may resolve to the same file
            resolved = str(Path(skill_path_str).resolve())
            if resolved in seen_skill_paths:
                continue
            seen_skill_paths.add(resolved)
            skill_path = Path(skill_path_str)
            rel_str = skill_path_str.replace("\\", "/")
            fixes.append({
                "check": "skillSideEffects",
                "file": rel_str,
                "action": "frontmatter_set",
                "key": "disable-model-invocation",
                "value": True,
                "description": f"Add disable-model-invocation to {skill_path.name}",
            })

    # Non-fixable: claudeMdLines — report in skipped
    for _ in _check_claudemd_lines(_find_claudemd_files(cwd)):
        skipped.append({
            "check": "claudeMdLines",
            "reason": "Cannot auto-fix: requires manual content reduction (run /doctor)",
        })

    # Non-fixable: skillListing — report in skipped
    from ccgate.model import DEFAULT_WINDOW_TOKENS
    total_chars = _measure_skill_descriptions(cwd)
    budget_fraction = config.get("skillListingBudgetFraction", 0.01)
    for _ in _check_skill_listing(total_chars, DEFAULT_WINDOW_TOKENS, budget_fraction):
        skipped.append({
            "check": "skillListing",
            "reason": "Cannot auto-fix: requires manual description reduction (run /skill-doctor)",
        })

    cwd_str = str(Path(cwd).resolve()).replace("\\", "/") if cwd else str(Path.cwd()).replace("\\", "/")

    try:
        from importlib.metadata import version as _pkg_version
        _version = _pkg_version("ccgate")
    except Exception:
        _version = "0.1.0"

    return {
        "generated": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cwd": cwd_str,
        "ccgate_version": _version,
        "fixes": fixes,
        "skipped": skipped,
        "applied": False,
        "applied_at": None,
        "startup_chars_before": None,
        "startup_chars_after": None,
        "startup_tokens_before_approx": None,
        "startup_tokens_after_approx": None,
        "startup_delta_approx": None,
    }


def apply_fixes(report: dict) -> dict:
    """Apply all staged fixes and return an updated report with applied=True.

    Measures startup overhead before and after applying. I3: delta is max(0, ...).
    """
    cwd = report.get("cwd")

    # Measure startup BEFORE applying fixes
    claudemd_before = _find_claudemd_files(cwd)
    chars_before = (
        sum(len(p.read_text(encoding="utf-8")) for p in claudemd_before if p.exists())
        + _measure_skill_descriptions(cwd)
    )

    for fix in report["fixes"]:
        file_path = _resolve_fix_path(fix["file"], cwd)
        action = fix["action"]
        if action == "json_set":
            _safe_json_patch(file_path, "set", fix["key"], fix["value"])
        elif action == "json_append":
            _safe_json_patch(file_path, "append", fix["key"], fix["value"])
        elif action == "create":
            _safe_create(file_path, fix["content"])
        elif action == "frontmatter_set":
            _safe_frontmatter_set(file_path, fix["key"], fix["value"])

    # Measure startup AFTER applying fixes
    claudemd_after = _find_claudemd_files(cwd)
    chars_after = (
        sum(len(p.read_text(encoding="utf-8")) for p in claudemd_after if p.exists())
        + _measure_skill_descriptions(cwd)
    )

    tokens_before = chars_before // 4
    tokens_after = chars_after // 4
    delta = max(0, tokens_before - tokens_after)

    return {
        **report,
        "applied": True,
        "applied_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "startup_chars_before": chars_before,
        "startup_chars_after": chars_after,
        "startup_tokens_before_approx": tokens_before,
        "startup_tokens_after_approx": tokens_after,
        "startup_delta_approx": delta,
    }


def run_shape(cwd: str | None = None, config: dict | None = None) -> list[dict]:
    """Run all static checks; return a list of finding dicts."""
    if config is None:
        from ccgate.config import DEFAULTS
        config = DEFAULTS

    findings: list[dict] = []

    claudemd_paths = _find_claudemd_files(cwd)
    findings.extend(_check_claudemd_lines(claudemd_paths))

    from ccgate.model import DEFAULT_WINDOW_TOKENS
    window_tokens = DEFAULT_WINDOW_TOKENS
    total_chars = _measure_skill_descriptions(cwd)
    budget_fraction = config.get("skillListingBudgetFraction", 0.01)
    findings.extend(_check_skill_listing(total_chars, window_tokens, budget_fraction))

    findings.extend(_check_skill_side_effects(cwd))
    findings.extend(_check_claudemd_excludes(cwd))

    settings = _load_settings(cwd)
    findings.extend(_check_cache_ttl(settings))
    findings.extend(_check_deny_reads(cwd, settings))
    findings.extend(_check_worktree_sparse(cwd, settings))
    findings.extend(_check_output_caps(cwd))
    findings.extend(_check_session_pinning(settings))
    findings.extend(_check_tool_deferral(settings, cwd))
    findings.extend(_summarize_tool_deferral(cwd))

    return findings


def _find_latest_fix_report(reports_dir: Path) -> Path | None:
    """Return the most recently modified fix-*.json file, or None if none exist."""
    candidates = list(reports_dir.glob("fix-*.json")) if reports_dir.exists() else []
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main(argv: list[str] | None = None) -> None:
    import argparse
    from ccgate.config import load_config

    parser = argparse.ArgumentParser(prog="ccgate shape")
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--json", action="store_true", dest="emit_json")
    parser.add_argument("--assert", action="store_true", dest="assert_mode",
                        help="Exit 1 on any error-severity finding")
    parser.add_argument("--fix", action="store_true",
                        help="Stage proposed fixes to ~/.ccgate/reports/")
    parser.add_argument("--apply", action="store_true",
                        help="Apply the most recent staged fix file")
    parser.add_argument("--fix-skills", action="store_true", dest="fix_skills",
                        help="Include skill frontmatter fixes (requires --fix)")
    args = parser.parse_args(argv)

    config = load_config(args.cwd)

    if args.fix or args.apply:
        reports_dir = Path.home() / ".ccgate" / "reports"
        report = None
        report_path = None

        if args.fix:
            report = stage_fixes(args.cwd, config, fix_skills=args.fix_skills)
            reports_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            report_path = reports_dir / f"fix-{ts}.json"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            if not report["fixes"] and not report["skipped"]:
                print("ccgate shape --fix: no fixable issues found")
            else:
                for fix in report["fixes"]:
                    print(f"  + [{fix['check']}] {fix['description']}  →  {fix['file']}")
                for sk in report["skipped"]:
                    print(f"  ~ [{sk['check']}] skipped: {sk['reason']}")
                if not args.apply:
                    print(f"\nStaged to {report_path}")
                    print("Run 'ccgate shape --apply' to apply these fixes.")

        if args.apply:
            if report is None:
                report_path = _find_latest_fix_report(reports_dir)
                if report_path is None:
                    print("ccgate shape --apply: no staged fix file found. Run --fix first.")
                    sys.exit(1)
                report = json.loads(report_path.read_text(encoding="utf-8"))
            updated = apply_fixes(report)
            report_path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
            delta = updated.get("startup_delta_approx") or 0
            print(f"Fixes applied. Startup overhead reduced by ~{delta:,} tokens (estimated).")
            if delta < 2000:
                print("  → Delta under 2,000 tokens. Repo was already lean — verify Phase 2 ROI before building.")
            else:
                print(f"  → Meaningful reduction. Phase 2 baseline captured in {report_path.name}")
        return

    findings = run_shape(args.cwd, config)

    if args.emit_json:
        print(json.dumps(findings, indent=2))
    else:
        if not findings:
            print("ccgate shape: no issues found")
        for f in findings:
            icon = "x" if f["severity"] == "error" else "!"
            print(f"  {icon} [{f['check']}] {f['message']}")

    if args.assert_mode and any(f["severity"] == "error" for f in findings):
        sys.exit(1)
