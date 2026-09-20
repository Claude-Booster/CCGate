import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


def encode_cwd(cwd: str) -> str:
    """Encode a working directory path to the Claude Code transcript directory name.

    Rules (matching Claude Code's own encoder):
    - On Windows paths (second char is ':'): lowercase the drive letter.
    - Replace each of ':', '\\', '/', '.', ' ' with '-'.
    """
    path = cwd
    if len(path) >= 2 and path[1] == ":":
        path = path[0].lower() + path[1:]
    return re.sub(r"[:\\/. ]", "-", path)


class CapabilityBand(Enum):
    BAND1_PRE_CACHE       = 1
    BAND2_CACHE_NO_CAUSES = 2
    BAND3_FULL            = 3


def detect_band(payload: dict) -> CapabilityBand:
    """Detect version capability band from a status-line payload (§4.1 discriminator rule)."""
    pc = payload.get("prompt_cache")
    if pc is None:
        return CapabilityBand.BAND1_PRE_CACHE
    if pc.get("miss_causes") is None:
        return CapabilityBand.BAND2_CACHE_NO_CAUSES
    return CapabilityBand.BAND3_FULL


@dataclass
class Usage:
    input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    output_tokens: int = 0
    ephemeral_1h_input_tokens: int = 0
    ephemeral_5m_input_tokens: int = 0


@dataclass
class Request:
    index: int
    timestamp: str
    model_id: str
    usage: Usage
    is_expected_rebuild: bool = False


def _parse_usage(msg: dict) -> Usage:
    u = msg.get("usage") or {}
    cc = u.get("cache_creation") or {}
    return Usage(
        input_tokens=                u.get("input_tokens")                or 0,
        cache_read_input_tokens=     u.get("cache_read_input_tokens")     or 0,
        cache_creation_input_tokens= u.get("cache_creation_input_tokens") or 0,
        output_tokens=               u.get("output_tokens")               or 0,
        ephemeral_1h_input_tokens=   cc.get("ephemeral_1h_input_tokens")  or 0,
        ephemeral_5m_input_tokens=   cc.get("ephemeral_5m_input_tokens")  or 0,
    )


def infer_ttl_from_usage(requests: list[Request]) -> int:
    """Infer session TTL in seconds from transcript usage fields (§4.2).

    ephemeral_5m_input_tokens > 0  →  300 s (API-key / cloud)
    ephemeral_1h_input_tokens > 0  →  3600 s (subscription)
    neither                        →  300 s (conservative default)
    """
    for r in requests:
        if r.usage.ephemeral_5m_input_tokens > 0:
            return 300
        if r.usage.ephemeral_1h_input_tokens > 0:
            return 3600
    return 300


def read_transcript(path: Path) -> list[Request]:
    """Parse a Claude Code JSONL transcript; return only assistant Request entries."""
    requests: list[Request] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            msg = entry.get("message") or {}
            requests.append(Request(
                index=len(requests),
                timestamp=entry.get("timestamp", ""),
                model_id=msg.get("model", "unknown"),
                usage=_parse_usage(msg),
            ))
    return requests


def find_transcripts(
    session_id: str | None = None,
    cwd: str | None = None,
) -> list[Path]:
    """Return JSONL paths for a session, all sessions in a cwd, or all sessions."""
    base = Path.home() / ".claude" / "projects"
    if cwd is not None:
        project_dir = base / encode_cwd(cwd)
        if not project_dir.exists():
            return []
        if session_id:
            p = project_dir / f"{session_id}.jsonl"
            return [p] if p.exists() else []
        return sorted(project_dir.glob("*.jsonl"))
    return sorted(base.rglob("*.jsonl"))


class Classification(Enum):
    HIT              = "HIT"
    MISS             = "MISS"
    EXPECTED_REBUILD = "EXPECTED_REBUILD"


_MISS_FRACTION   = 0.05
_MISS_MIN_TOKENS = 2_000
_COMPACT_SIGNAL  = "/compact"
_CLEAR_SIGNAL    = "/clear"


def _scan_compact_turns(path: Path) -> set[int]:
    """Single-pass scan: return the set of assistant turn indices preceded by /compact or /clear.

    Handles both string content and list-of-parts content. O(N) — reads the file once.
    """
    compact_indices: set[int] = set()
    assistant_index = 0
    pending_compact = False
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entry_type = e.get("type")
                if entry_type == "user":
                    content = e.get("message", {}).get("content", "")
                    text = content if isinstance(content, str) else str(content)
                    pending_compact = _COMPACT_SIGNAL in text or _CLEAR_SIGNAL in text
                elif entry_type == "assistant":
                    if pending_compact:
                        compact_indices.add(assistant_index)
                    assistant_index += 1
                    pending_compact = False
    except OSError:
        pass
    return compact_indices


def classify_requests(
    requests: list[Request],
    transcript_path: Path | None = None,
) -> list[tuple[Request, Classification]]:
    """Classify each assistant request as HIT, MISS, or EXPECTED_REBUILD.

    EXPECTED_REBUILD: the user sent /compact or /clear before this turn, OR
    req.is_expected_rebuild is set (from external signal, Phase 1+).
    MISS: re_processed > 5% of expected_cache AND > 2000 tokens.
    HIT: everything else.
    """
    compact_indices: set[int] = (
        _scan_compact_turns(transcript_path) if transcript_path is not None else set()
    )
    results: list[tuple[Request, Classification]] = []
    expected_cache = 0

    for req in requests:
        is_rebuild = req.is_expected_rebuild or req.index in compact_indices

        if is_rebuild:
            results.append((req, Classification.EXPECTED_REBUILD))
            expected_cache = req.usage.cache_creation_input_tokens
            continue

        if expected_cache == 0:
            results.append((req, Classification.HIT))
            expected_cache = (req.usage.cache_read_input_tokens
                              + req.usage.cache_creation_input_tokens)
            continue

        re_processed = max(0, expected_cache - req.usage.cache_read_input_tokens)
        is_miss = (
            re_processed > _MISS_FRACTION * expected_cache
            and re_processed >= _MISS_MIN_TOKENS
        )

        if is_miss:
            results.append((req, Classification.MISS))
            expected_cache = req.usage.cache_creation_input_tokens
        else:
            results.append((req, Classification.HIT))
            expected_cache = (req.usage.cache_read_input_tokens
                              + req.usage.cache_creation_input_tokens)

    return results
