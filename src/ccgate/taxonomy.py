D1_MODEL_SWITCH         = "D1.model_switch"
D1_EFFORT_CHANGE        = "D1.effort_change"
D1_TOOLS_CHANGED        = "D1.tools_changed"
D1_SYSTEM_PROMPT_CHANGED = "D1.system_prompt_changed"
D1_FAST_MODE_TOGGLE     = "D1.fast_mode_toggle"
D1_TTL_EXPIRED          = "D1.ttl_expired"
D1_IMAGE_EVICTION       = "D1.image_eviction"
D1_UNCLASSIFIED         = "D1.unclassified"

D2_COMPACTION           = "D2.compaction"
D2_TOOL_RESULT_CLEARING = "D2.tool_result_clearing"

D3_REREAD               = "D3.reread"
D3_BLOCKED_PATH         = "D3.blocked_path"
D3_FULL_READ_LARGE      = "D3.full_read_large"
D3_UNBOUNDED_OUTPUT     = "D3.unbounded_output"

D4_CLAUDEMD_BLOAT       = "D4.claudemd_bloat"
D4_SKILL_LISTING        = "D4.skill_listing"
D4_MCP_UNUSED           = "D4.mcp_unused"
D4_RULES_UNSCOPED       = "D4.rules_unscoped"

D5_MAIN_CONTEXT_EXPLORATION = "D5.main_context_exploration"

D1_ALL: list[str] = [
    D1_MODEL_SWITCH, D1_EFFORT_CHANGE, D1_TOOLS_CHANGED,
    D1_SYSTEM_PROMPT_CHANGED, D1_FAST_MODE_TOGGLE,
    D1_TTL_EXPIRED, D1_IMAGE_EVICTION, D1_UNCLASSIFIED,
]

FIX_HINTS: dict[str, str] = {
    D1_MODEL_SWITCH:          "pin model at session start",
    D1_EFFORT_CHANGE:         "avoid /effort mid-session",
    D1_TOOLS_CHANGED:         "start MCP servers at launch",
    D1_SYSTEM_PROMPT_CHANGED: "restart after Claude Code upgrade",
    D1_FAST_MODE_TOGGLE:      "avoid toggling fast mode mid-session",
    D1_TTL_EXPIRED:           "/compact before stepping away",
    D1_IMAGE_EVICTION:        "reduce image count per session",
    D1_UNCLASSIFIED:          "cause attribution unavailable here (statusline blocked by policy) — escalate or use the Track B runner",
    D2_COMPACTION:            "expected — no action needed",
    D2_TOOL_RESULT_CLEARING:  "expected — no action needed",
}
