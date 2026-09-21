"""shape.py — static config lint (Phase 0). Checks: G2 (claudeMdLines), G3 (skillListing)."""
import json
import os
import datetime
import sys
from pathlib import Path

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
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


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
            skill_path_str = finding["message"].split(":")[0].strip()
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

    return findings


def main(argv: list[str] | None = None) -> None:
    import argparse
    from ccgate.config import load_config

    parser = argparse.ArgumentParser(prog="ccgate shape")
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--json", action="store_true", dest="emit_json")
    parser.add_argument("--assert", action="store_true", dest="assert_mode",
                        help="Exit 1 on any error-severity finding")
    args = parser.parse_args(argv)

    config = load_config(args.cwd)
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
