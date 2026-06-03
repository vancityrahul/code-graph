from investigator.agents.registry import AgentRegistry, AGENT_NAMES


def test_all_agents_defined():
    for name in AGENT_NAMES:
        spec = AgentRegistry.get(name)
        assert spec is not None, f"Agent '{name}' not in registry"


def test_orchestrator_has_delegate_tool():
    spec = AgentRegistry.get("orchestrator")
    assert "delegate_to_subagent" in spec.tool_names


def test_orchestrator_has_memory_tools():
    spec = AgentRegistry.get("orchestrator")
    assert "memory_save" in spec.tool_names
    assert "memory_search" in spec.tool_names


def test_subagents_do_not_have_delegate():
    for name in ["code_explorer", "test_analyst", "security_auditor", "doc_synthesizer"]:
        spec = AgentRegistry.get(name)
        assert "delegate_to_subagent" not in spec.tool_names, f"{name} should not delegate"


def test_doc_synthesizer_has_no_tools():
    spec = AgentRegistry.get("doc_synthesizer")
    assert spec.tool_names == []


def test_budget_fraction_is_sane():
    spec = AgentRegistry.get("code_explorer")
    assert 0 < spec.max_turns <= 20
