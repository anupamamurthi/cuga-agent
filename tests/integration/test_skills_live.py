"""
Live smoke tests for the skills-as-markdown feature across all supported LLM providers.

These tests hit real LLM endpoints. Each provider is gated behind a pytest mark and
will be SKIPPED automatically when the required environment variables are not set.

Run options
-----------

# Run all providers that have credentials available:
uv run pytest tests/integration/test_skills_live.py -v

# Run only a specific provider:
uv run pytest tests/integration/test_skills_live.py -v -m openai
uv run pytest tests/integration/test_skills_live.py -v -m watsonx
uv run pytest tests/integration/test_skills_live.py -v -m rits
uv run pytest tests/integration/test_skills_live.py -v -m litellm

Required environment variables per provider
-------------------------------------------

OpenAI:
  OPENAI_API_KEY

WatsonX:
  WATSONX_PROJECT_ID          (required by ChatWatsonx)
  WATSONX_APIKEY  or
  WATSONX_TOKEN               (one of these, handled by langchain-ibm)
  WATSONX_URL                 (optional, defaults to us-south)
  WATSONX_MODEL               (optional, defaults to meta-llama/llama-4-maverick-17b-128e-instruct-fp8)

RITS:
  RITS_API_KEY
  RITS_URL                    (e.g. http://your-rits-host:4000)
  RITS_MODEL                  (optional, defaults to rits/openai/gpt-oss-120b)

LiteLLM:
  LITELLM_API_KEY             (forwarded as api_key — can be OPENAI_API_KEY compatible)
  LITELLM_BASE_URL            (optional, e.g. http://localhost:4000)
  LITELLM_MODEL               (optional, defaults to gpt-4o-mini)
"""

import os
import asyncio
import pytest
from pathlib import Path
from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# Provider detection helpers
# ---------------------------------------------------------------------------

def _have_openai() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _have_watsonx() -> bool:
    return bool(
        os.environ.get("WATSONX_PROJECT_ID")
        and (os.environ.get("WATSONX_APIKEY") or os.environ.get("WATSONX_TOKEN"))
    )


def _have_rits() -> bool:
    return bool(os.environ.get("RITS_API_KEY") and os.environ.get("RITS_URL"))


def _have_litellm() -> bool:
    return bool(os.environ.get("LITELLM_API_KEY") or os.environ.get("OPENAI_API_KEY"))


# ---------------------------------------------------------------------------
# Model factories — each returns a BaseChatModel ready to pass to CugaAgent
# ---------------------------------------------------------------------------

def _make_openai_model():
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        api_key=os.environ["OPENAI_API_KEY"],
        max_tokens=512,
        temperature=0.0,
    )


def _make_watsonx_model():
    from langchain_ibm import ChatWatsonx
    return ChatWatsonx(
        model_id=os.environ.get(
            "WATSONX_MODEL", "meta-llama/llama-4-maverick-17b-128e-instruct-fp8"
        ),
        project_id=os.environ["WATSONX_PROJECT_ID"],
        temperature=0.0,
        max_tokens=512,
    )


def _make_rits_model():
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        api_key=os.environ["RITS_API_KEY"],
        base_url=os.environ["RITS_URL"],
        model=os.environ.get("RITS_MODEL", "rits/openai/gpt-oss-120b"),
        max_tokens=512,
        temperature=0.0,
        seed=42,
    )


def _make_litellm_model():
    try:
        from langchain_litellm import ChatLiteLLM
    except ImportError:
        pytest.skip("langchain-litellm not installed")

    import litellm
    litellm.drop_params = True

    params = {
        "model": os.environ.get("LITELLM_MODEL", "gpt-4o-mini"),
        "temperature": 0.0,
        "max_tokens": 512,
        "drop_params": True,
    }
    base_url = os.environ.get("LITELLM_BASE_URL")
    if base_url:
        params["api_base"] = base_url.rstrip("/")
        params["custom_llm_provider"] = "openai"

    api_key = os.environ.get("LITELLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key:
        params["api_key"] = api_key

    return ChatLiteLLM(**params)


# ---------------------------------------------------------------------------
# Shared test infrastructure
# ---------------------------------------------------------------------------

MARKER_SKILL_CONTENT = """\
# Response Format Skill

Whenever returning a final answer, you MUST end it with the exact phrase:
SKILL_VERIFIED_OK

This is a required formatting rule. No exceptions.
"""

DATE_SKILL_CONTENT = """\
# Date Formatting Skill

Whenever a tool returns a Unix timestamp (integer number of seconds since epoch),
convert it to a human-readable date in the format YYYY-MM-DD before displaying it.
Never show raw epoch numbers in the final answer.
"""


def _make_skill_file(skills_dir: Path, name: str, content: str):
    p = skills_dir / name
    p.write_text(content, encoding="utf-8")
    return p


@tool
def echo_tool(message: str) -> str:
    """Echo the message back to the caller."""
    return f"Echo: {message}"


@tool
def get_record(record_id: str) -> dict:
    """Retrieve a record by ID. Returns record data including a timestamp."""
    return {
        "id": record_id,
        "name": "Test Record",
        "created_at": 1748000000,  # Unix epoch — skills should convert this
        "value": 42,
    }


async def _run_agent(model, skills_dir: str, user_message: str) -> str:
    from cuga.sdk import CugaAgent
    agent = CugaAgent(
        tools=[echo_tool, get_record],
        model=model,
        skills_dir=skills_dir,
        auto_load_policies=False,
    )
    result = await agent.invoke(user_message)
    return result.output if result else ""


# ---------------------------------------------------------------------------
# OpenAI tests
# ---------------------------------------------------------------------------

@pytest.mark.openai
@pytest.mark.skipif(not _have_openai(), reason="OPENAI_API_KEY not set")
class TestSkillsLiveOpenAI:
    def test_skills_loaded_into_agent(self, tmp_path):
        """Verify skills are loaded onto the agent object (no LLM call needed)."""
        from cuga.sdk import CugaAgent
        _make_skill_file(tmp_path, "marker.md", MARKER_SKILL_CONTENT)
        agent = CugaAgent(
            tools=[echo_tool],
            model=_make_openai_model(),
            skills_dir=str(tmp_path),
            auto_load_policies=False,
        )
        assert "SKILL_VERIFIED_OK" in agent._skills
        assert "Response Format Skill" in agent._skills

    @pytest.mark.asyncio
    async def test_marker_skill_influences_response(self, tmp_path):
        """Agent response should contain the marker the skill requires."""
        _make_skill_file(tmp_path, "marker.md", MARKER_SKILL_CONTENT)
        response = await _run_agent(
            _make_openai_model(),
            str(tmp_path),
            "Echo the word hello back to me.",
        )
        assert "SKILL_VERIFIED_OK" in response, (
            f"Expected 'SKILL_VERIFIED_OK' in response but got:\n{response}"
        )

    @pytest.mark.asyncio
    async def test_date_formatting_skill_converts_timestamp(self, tmp_path):
        """Date formatting skill should cause epoch to appear as a readable date."""
        _make_skill_file(tmp_path, "dates.md", DATE_SKILL_CONTENT)
        response = await _run_agent(
            _make_openai_model(),
            str(tmp_path),
            "Retrieve record with id 'rec-001' and show me when it was created.",
        )
        # Raw epoch should NOT appear; a year like 2025 should
        assert "1748000000" not in response, (
            f"Raw epoch timestamp appeared in response:\n{response}"
        )
        assert "2025" in response, (
            f"Expected converted year '2025' in response but got:\n{response}"
        )

    @pytest.mark.asyncio
    async def test_no_skills_agent_still_works(self, tmp_path):
        """Agent without skills should still complete tasks normally."""
        from cuga.sdk import CugaAgent
        agent = CugaAgent(
            tools=[echo_tool],
            model=_make_openai_model(),
            cuga_folder=str(tmp_path / ".cuga"),  # empty cuga folder, no skills
            auto_load_policies=False,
        )
        assert agent._skills == ""
        result = await agent.invoke("Echo the word 'baseline' back to me.")
        assert result.output, "Expected a non-empty response"


# ---------------------------------------------------------------------------
# WatsonX tests
# ---------------------------------------------------------------------------

@pytest.mark.watsonx
@pytest.mark.skipif(not _have_watsonx(), reason="WATSONX_PROJECT_ID / WATSONX_APIKEY not set")
class TestSkillsLiveWatsonX:
    def test_skills_loaded_into_agent(self, tmp_path):
        from cuga.sdk import CugaAgent
        _make_skill_file(tmp_path, "marker.md", MARKER_SKILL_CONTENT)
        agent = CugaAgent(
            tools=[echo_tool],
            model=_make_watsonx_model(),
            skills_dir=str(tmp_path),
            auto_load_policies=False,
        )
        assert "SKILL_VERIFIED_OK" in agent._skills

    @pytest.mark.asyncio
    async def test_marker_skill_influences_response(self, tmp_path):
        _make_skill_file(tmp_path, "marker.md", MARKER_SKILL_CONTENT)
        response = await _run_agent(
            _make_watsonx_model(),
            str(tmp_path),
            "Echo the word hello back to me.",
        )
        assert "SKILL_VERIFIED_OK" in response, (
            f"Expected 'SKILL_VERIFIED_OK' in response:\n{response}"
        )

    @pytest.mark.asyncio
    async def test_date_formatting_skill_converts_timestamp(self, tmp_path):
        _make_skill_file(tmp_path, "dates.md", DATE_SKILL_CONTENT)
        response = await _run_agent(
            _make_watsonx_model(),
            str(tmp_path),
            "Retrieve record with id 'rec-001' and tell me the creation date.",
        )
        assert "1748000000" not in response, (
            f"Raw epoch timestamp appeared:\n{response}"
        )


# ---------------------------------------------------------------------------
# RITS tests
# ---------------------------------------------------------------------------

@pytest.mark.rits
@pytest.mark.skipif(not _have_rits(), reason="RITS_API_KEY / RITS_URL not set")
class TestSkillsLiveRITS:
    def test_skills_loaded_into_agent(self, tmp_path):
        from cuga.sdk import CugaAgent
        _make_skill_file(tmp_path, "marker.md", MARKER_SKILL_CONTENT)
        agent = CugaAgent(
            tools=[echo_tool],
            model=_make_rits_model(),
            skills_dir=str(tmp_path),
            auto_load_policies=False,
        )
        assert "SKILL_VERIFIED_OK" in agent._skills

    @pytest.mark.asyncio
    async def test_marker_skill_influences_response(self, tmp_path):
        _make_skill_file(tmp_path, "marker.md", MARKER_SKILL_CONTENT)
        response = await _run_agent(
            _make_rits_model(),
            str(tmp_path),
            "Echo the word hello back to me.",
        )
        assert "SKILL_VERIFIED_OK" in response, (
            f"Expected 'SKILL_VERIFIED_OK' in response:\n{response}"
        )

    @pytest.mark.asyncio
    async def test_date_formatting_skill_converts_timestamp(self, tmp_path):
        _make_skill_file(tmp_path, "dates.md", DATE_SKILL_CONTENT)
        response = await _run_agent(
            _make_rits_model(),
            str(tmp_path),
            "Retrieve record with id 'rec-001' and tell me the creation date.",
        )
        assert "1748000000" not in response, (
            f"Raw epoch timestamp appeared:\n{response}"
        )


# ---------------------------------------------------------------------------
# LiteLLM tests
# ---------------------------------------------------------------------------

@pytest.mark.litellm
@pytest.mark.skipif(not _have_litellm(), reason="LITELLM_API_KEY / OPENAI_API_KEY not set")
class TestSkillsLiveLiteLLM:
    def test_skills_loaded_into_agent(self, tmp_path):
        from cuga.sdk import CugaAgent
        _make_skill_file(tmp_path, "marker.md", MARKER_SKILL_CONTENT)
        agent = CugaAgent(
            tools=[echo_tool],
            model=_make_litellm_model(),
            skills_dir=str(tmp_path),
            auto_load_policies=False,
        )
        assert "SKILL_VERIFIED_OK" in agent._skills

    @pytest.mark.asyncio
    async def test_marker_skill_influences_response(self, tmp_path):
        _make_skill_file(tmp_path, "marker.md", MARKER_SKILL_CONTENT)
        response = await _run_agent(
            _make_litellm_model(),
            str(tmp_path),
            "Echo the word hello back to me.",
        )
        assert "SKILL_VERIFIED_OK" in response, (
            f"Expected 'SKILL_VERIFIED_OK' in response:\n{response}"
        )

    @pytest.mark.asyncio
    async def test_date_formatting_skill_converts_timestamp(self, tmp_path):
        _make_skill_file(tmp_path, "dates.md", DATE_SKILL_CONTENT)
        response = await _run_agent(
            _make_litellm_model(),
            str(tmp_path),
            "Retrieve record with id 'rec-001' and tell me the creation date.",
        )
        assert "1748000000" not in response, (
            f"Raw epoch timestamp appeared:\n{response}"
        )


# ---------------------------------------------------------------------------
# Provider-agnostic: skills structure validation (no LLM call)
# These run regardless of provider credentials.
# ---------------------------------------------------------------------------

class TestSkillsPromptStructure:
    """
    These tests verify that the full pipeline — SkillsManager → create_mcp_prompt →
    jinja2 template — produces a correctly structured prompt for each realistic
    skill type, without hitting a real LLM.
    """

    @pytest.fixture
    def mcp_template(self):
        from jinja2 import Template
        path = (
            Path(__file__).parent.parent.parent
            / "src/cuga/backend/cuga_graph/nodes/cuga_lite/prompts/mcp_prompt.jinja2"
        )
        return Template(path.read_text())

    def _render(self, mcp_template, skills):
        return mcp_template.render(
            base_prompt=None, apps=[], allow_user_clarification=True,
            return_to_user_cases=None, instructions=None, tools=[],
            task_loaded_from_file=False, is_autonomous_subtask=False,
            enable_find_tools=False, special_instructions=None, skills=skills,
        )

    def test_all_example_skills_render_correctly(self, mcp_template):
        from cuga.configurations.skills_manager import SkillsManager
        examples = (
            Path(__file__).parent.parent.parent
            / "src/cuga/configurations/skills/examples"
        )
        skills = SkillsManager.load_from_directory(examples)
        rendered = self._render(mcp_template, skills)

        assert "Currency Conversion" in rendered
        assert "Data Privacy" in rendered
        assert "Date Formatting" in rendered
        # Skills appear before the tools section
        assert rendered.index("# SKILLS") < rendered.index("# Current Available Tools")

    def test_skills_section_between_instructions_and_rules(self, tmp_path, mcp_template):
        """
        Structural contract: skills sit between special_instructions and Critical Rules.
        This ensures skills are in a prominent, early position in the system prompt.
        """
        from cuga.configurations.skills_manager import SkillsManager
        _make_skill_file(tmp_path, "s.md", "# My Skill\n\nSKILL_TEXT")
        skills = SkillsManager.load_from_directory(tmp_path)
        rendered = mcp_template.render(
            base_prompt=None, apps=[], allow_user_clarification=True,
            return_to_user_cases=None, instructions=None, tools=[],
            task_loaded_from_file=False, is_autonomous_subtask=False,
            enable_find_tools=False, special_instructions="POLICY_TEXT", skills=skills,
        )
        policy_pos = rendered.index("POLICY_TEXT")
        skill_pos = rendered.index("SKILL_TEXT")
        rules_pos = rendered.index("## Critical Rules")
        assert policy_pos < skill_pos < rules_pos

    @pytest.mark.parametrize("provider_label,model_factory,have_fn", [
        ("openai", "_make_openai_model", "_have_openai"),
        ("watsonx", "_make_watsonx_model", "_have_watsonx"),
        ("rits", "_make_rits_model", "_have_rits"),
        ("litellm", "_make_litellm_model", "_have_litellm"),
    ])
    def test_agent_init_with_skills_for_each_provider(
        self, tmp_path, provider_label, model_factory, have_fn
    ):
        """
        CugaAgent initialisation with skills works for every provider that
        has credentials available. Skips providers that don't.
        """
        import sys
        this_module = sys.modules[__name__]
        have = getattr(this_module, have_fn)()
        if not have:
            pytest.skip(f"{provider_label} credentials not available")

        from cuga.sdk import CugaAgent
        factory = getattr(this_module, model_factory)
        model = factory()

        _make_skill_file(tmp_path, "test.md", "# Test Skill\n\nTest guidance.")
        agent = CugaAgent(
            tools=[echo_tool],
            model=model,
            skills_dir=str(tmp_path),
            auto_load_policies=False,
        )
        assert "Test Skill" in agent._skills
        assert "Test guidance." in agent._skills


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
