from __future__ import annotations
from dataclasses import dataclass
from investigator.agents.prompts import (
    ORCHESTRATOR, CODE_EXPLORER, TEST_ANALYST, SECURITY_AUDITOR, DOC_SYNTHESIZER
)

AGENT_NAMES = ["orchestrator", "code_explorer", "test_analyst", "security_auditor", "doc_synthesizer"]


@dataclass
class AgentSpec:
    name: str
    system_prompt: str
    tool_names: list[str]
    max_turns: int


_SPECS: dict[str, AgentSpec] = {
    "orchestrator": AgentSpec(
        name="orchestrator",
        system_prompt=ORCHESTRATOR,
        tool_names=[
            "index_repo", "index_status",
            "find_symbol", "get_callers", "get_callees", "find_references",
            "get_imports", "get_file_summary", "find_path", "get_test_coverage",
            "read_file", "grep", "list_dir",
            "memory_save", "memory_search",
            "delegate_to_subagent",
        ],
        max_turns=25,
    ),
    "code_explorer": AgentSpec(
        name="code_explorer",
        system_prompt=CODE_EXPLORER,
        tool_names=[
            "index_repo", "index_status",
            "find_symbol", "get_callers", "get_callees", "find_references",
            "get_imports", "get_file_summary", "find_path", "get_test_coverage",
            "query_graph",
            "read_file", "grep", "list_dir",
        ],
        max_turns=15,
    ),
    "test_analyst": AgentSpec(
        name="test_analyst",
        system_prompt=TEST_ANALYST,
        tool_names=["get_test_coverage", "find_references", "grep", "read_file"],
        max_turns=12,
    ),
    "security_auditor": AgentSpec(
        name="security_auditor",
        system_prompt=SECURITY_AUDITOR,
        tool_names=["find_path", "find_references", "grep", "read_file"],
        max_turns=12,
    ),
    "doc_synthesizer": AgentSpec(
        name="doc_synthesizer",
        system_prompt=DOC_SYNTHESIZER,
        tool_names=[],
        max_turns=5,
    ),
}


class AgentRegistry:
    @staticmethod
    def get(name: str) -> AgentSpec | None:
        return _SPECS.get(name)
