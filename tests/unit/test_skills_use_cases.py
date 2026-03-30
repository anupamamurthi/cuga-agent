"""
Use-case-driven tests for the skills-as-markdown feature.

These tests are not about SkillsManager internals — they demonstrate the breadth
of what the feature unlocks: what kinds of skills work, how they compose, how they
interact with the rest of the prompt, and what real-world authoring patterns are valid.

Each test class maps to a concrete use-case category.
"""

import pytest
from pathlib import Path
from jinja2 import Template


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _skill(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _load(skills_dir):
    from cuga_skills.loader import SkillsManager
    return SkillsManager.load_from_directory(skills_dir)


@pytest.fixture
def mcp_template():
    path = (
        Path(__file__).parent.parent.parent
        / "src/cuga/backend/cuga_graph/nodes/cuga_lite/prompts/mcp_prompt.jinja2"
    )
    return Template(path.read_text())


def _render(mcp_template, skills=None, special_instructions=None):
    return mcp_template.render(
        base_prompt=None,
        apps=[],
        allow_user_clarification=True,
        return_to_user_cases=None,
        instructions=None,
        tools=[],
        task_loaded_from_file=False,
        is_autonomous_subtask=False,
        enable_find_tools=False,
        special_instructions=special_instructions,
        skills=skills,
    )


# ---------------------------------------------------------------------------
# Use case 1: Workflow choreography
# Skills can encode step-by-step business workflows the agent must follow.
# ---------------------------------------------------------------------------

class TestWorkflowChoreographySkill:
    """
    Domain: e.g. a financial ops team needs the agent to always follow a
    specific sequence for refund processing — check order, check eligibility,
    then process.
    """

    SKILL = """\
# Refund Processing Workflow

Applies when the user requests a refund or return for any order.

## Required sequence
Follow these steps in order. Do not skip or reorder.

1. Retrieve the order using `get_order(order_id)`
2. Check return eligibility using `check_return_eligibility(order_id)`
3. If eligible, call `process_refund(order_id, amount)` with the exact amount
4. Confirm by calling `get_refund_status(refund_id)` and include the status in the answer

## Never do
- Do not call `process_refund` before checking eligibility
- Do not assume eligibility from order status alone
"""

    def test_workflow_skill_loads(self, tmp_path):
        _skill(tmp_path, "refund_workflow.md", self.SKILL)
        result = _load(tmp_path)
        assert "Refund Processing Workflow" in result
        assert "check_return_eligibility" in result

    def test_workflow_step_sequence_preserved(self, tmp_path):
        _skill(tmp_path, "refund_workflow.md", self.SKILL)
        result = _load(tmp_path)
        # Steps must appear in order
        pos_check = result.index("check_return_eligibility")
        pos_process = result.index("process_refund")
        assert pos_check < pos_process

    def test_workflow_skill_in_prompt(self, tmp_path, mcp_template):
        _skill(tmp_path, "refund_workflow.md", self.SKILL)
        skills = _load(tmp_path)
        rendered = _render(mcp_template, skills=skills)
        assert "Refund Processing Workflow" in rendered
        assert "Do not call `process_refund` before checking eligibility" in rendered


# ---------------------------------------------------------------------------
# Use case 2: Cross-tool semantic mapping
# Skills can describe how field names map across APIs that use different
# terminology for the same concept.
# ---------------------------------------------------------------------------

class TestCrossToolSemanticMappingSkill:
    """
    Domain: CRM + Billing integration where the same entity has different
    field names in each system.
    """

    SKILL = """\
# CRM ↔ Billing Field Mapping

Use this mapping when a task spans both the CRM and Billing APIs.

| CRM field        | Billing field    | Notes                          |
|------------------|------------------|--------------------------------|
| `customer_id`    | `account_ref`    | Always use CRM id as the key   |
| `contract_start` | `activation_date`| Both are ISO 8601 UTC          |
| `plan_code`      | `product_sku`    | Exact match, no transformation |

When cross-referencing between systems, always look up the CRM `customer_id`
first and use it as `account_ref` in billing queries.
"""

    def test_mapping_table_preserved_in_output(self, tmp_path):
        _skill(tmp_path, "field_mapping.md", self.SKILL)
        result = _load(tmp_path)
        assert "customer_id" in result
        assert "account_ref" in result
        assert "activation_date" in result

    def test_mapping_skill_in_prompt(self, tmp_path, mcp_template):
        _skill(tmp_path, "field_mapping.md", self.SKILL)
        skills = _load(tmp_path)
        rendered = _render(mcp_template, skills=skills)
        # The table content and the instruction must both appear
        assert "account_ref" in rendered
        assert "Always use CRM id as the key" in rendered


# ---------------------------------------------------------------------------
# Use case 3: Safe defaults for destructive operations
# Skills can encode soft confirmation checks without needing a hard policy.
# ---------------------------------------------------------------------------

class TestDestructiveOperationGuardSkill:
    """
    Domain: An ops agent that can delete records — the team wants a soft
    confirmation check without wiring up a full ToolApproval policy.
    """

    SKILL = """\
# Confirmation Required for Destructive Actions

Before calling any tool whose name contains `delete`, `archive`, `purge`, or `remove`:

1. Check the user's message for explicit confirmation signals:
   - "yes", "confirm", "go ahead", "do it", "proceed"
2. If no confirmation signal is present, return to the user and ask:
   "This will permanently [action]. Please confirm with 'yes' to proceed."
3. Only proceed after receiving confirmation.

This applies even in autonomous subtask mode.
"""

    def test_guard_skill_loads(self, tmp_path):
        _skill(tmp_path, "destructive_guard.md", self.SKILL)
        result = _load(tmp_path)
        assert "Confirmation Required for Destructive Actions" in result
        assert "delete" in result

    def test_guard_skill_in_prompt(self, tmp_path, mcp_template):
        _skill(tmp_path, "destructive_guard.md", self.SKILL)
        skills = _load(tmp_path)
        rendered = _render(mcp_template, skills=skills)
        assert "Please confirm with" in rendered
        assert "purge" in rendered


# ---------------------------------------------------------------------------
# Use case 4: Localization / regional rules
# ---------------------------------------------------------------------------

class TestLocalizationSkill:
    """
    Domain: EU market agent. Different date format, VAT display rules,
    and currency formatting than a US agent.
    """

    SKILL = """\
# EU Market Formatting Rules

This agent serves the European market. Apply these rules to all output.

## Dates
- Format: DD/MM/YYYY (not YYYY-MM-DD)
- Always show timezone: `15/06/2025 14:30 CEST`

## Currency
- Always show EUR symbol before the amount: `€ 1.234,56`
- Use period as thousands separator, comma as decimal separator
- VAT must be shown separately: `Net: € 1.000,00 | VAT (20%): € 200,00 | Total: € 1.200,00`

## Language
- Respond in the same language the user writes in
- Default to English if language is ambiguous
"""

    def test_localization_skill_loads(self, tmp_path):
        _skill(tmp_path, "eu_locale.md", self.SKILL)
        result = _load(tmp_path)
        assert "EU Market Formatting Rules" in result
        assert "DD/MM/YYYY" in result
        assert "VAT" in result

    def test_localization_in_prompt(self, tmp_path, mcp_template):
        _skill(tmp_path, "eu_locale.md", self.SKILL)
        skills = _load(tmp_path)
        rendered = _render(mcp_template, skills=skills)
        assert "DD/MM/YYYY" in rendered
        assert "VAT must be shown separately" in rendered


# ---------------------------------------------------------------------------
# Use case 5: Environment awareness
# Skills can make the agent behave differently in staging vs production.
# ---------------------------------------------------------------------------

class TestEnvironmentAwarenessSkill:
    """
    Domain: The same agent binary deploys to staging and prod. A skill file
    is dropped in by the deployment pipeline to signal the environment.
    """

    STAGING_SKILL = """\
# Staging Environment Notice

This agent is connected to staging/test systems. All data is synthetic.

## Required behaviour
- Prefix every final answer with: ⚠️ **STAGING DATA — not production-accurate**
- Never suggest that results are final or should be acted upon
- Log a note in the answer: "Connected to: staging API endpoints"
"""

    def test_staging_skill_prefix_content(self, tmp_path):
        _skill(tmp_path, "staging_notice.md", self.STAGING_SKILL)
        result = _load(tmp_path)
        assert "STAGING DATA" in result
        assert "staging API endpoints" in result

    def test_staging_skill_rendered(self, tmp_path, mcp_template):
        _skill(tmp_path, "staging_notice.md", self.STAGING_SKILL)
        skills = _load(tmp_path)
        rendered = _render(mcp_template, skills=skills)
        assert "STAGING DATA" in rendered
        assert "not production-accurate" in rendered


# ---------------------------------------------------------------------------
# Use case 6: Multi-tenant persona / brand voice
# Each customer gets their own skill file with their brand voice rules.
# ---------------------------------------------------------------------------

class TestBrandVoiceSkill:
    """
    Domain: SaaS product serving multiple enterprise customers, each with
    different communication guidelines.
    """

    ACME_SKILL = """\
# ACME Corp Communication Style

## Tone
- Formal and concise. Never use contractions ("don't" → "do not").
- Address the user as "you" — never by name unless they provide it.

## Format
- All monetary figures: USD, 2 decimal places, comma-separated thousands
  Example: $1,234,567.00
- Lists must be numbered, not bulleted
- Maximum response length: 200 words

## Forbidden phrases
- Do not use: "awesome", "fantastic", "great job", "no problem"
- Use instead: "confirmed", "completed", "noted", "understood"
"""

    def test_brand_voice_skill_loads(self, tmp_path):
        _skill(tmp_path, "brand_voice.md", self.ACME_SKILL)
        result = _load(tmp_path)
        assert "ACME Corp Communication Style" in result
        assert "contractions" in result
        assert "Forbidden phrases" in result

    def test_brand_voice_in_prompt(self, tmp_path, mcp_template):
        _skill(tmp_path, "brand_voice.md", self.ACME_SKILL)
        skills = _load(tmp_path)
        rendered = _render(mcp_template, skills=skills)
        assert "contractions" in rendered
        assert "Forbidden phrases" in rendered


# ---------------------------------------------------------------------------
# Use case 7: API quirk documentation
# Encode tribal knowledge about specific API behaviours.
# ---------------------------------------------------------------------------

class TestAPIQuirkSkill:
    """
    Domain: A vendor API has known quirks that need special handling. This
    knowledge would otherwise live in Slack threads or engineers' heads.
    """

    SKILL = """\
# Acme CRM API Known Quirks

## Pagination edge case
The `/contacts` endpoint returns `total_count: 0` even when there ARE results
on subsequent pages. Do NOT use `total_count` to decide when to stop paginating.
Instead, stop when the returned `items` array is empty.

## Timestamp format bug
The `created_at` field is returned as a Unix timestamp in **milliseconds**,
not seconds — despite the API docs saying seconds. Always divide by 1000
before converting to a human-readable date.

## Soft-delete behaviour
Deleted records are returned by default. Always add `?include_deleted=false`
to list queries unless the user explicitly asks for deleted records.
"""

    def test_quirk_skill_loads(self, tmp_path):
        _skill(tmp_path, "acme_crm_quirks.md", self.SKILL)
        result = _load(tmp_path)
        assert "Acme CRM API Known Quirks" in result
        assert "milliseconds" in result
        assert "total_count" in result

    def test_quirk_skill_in_prompt(self, tmp_path, mcp_template):
        _skill(tmp_path, "acme_crm_quirks.md", self.SKILL)
        skills = _load(tmp_path)
        rendered = _render(mcp_template, skills=skills)
        assert "milliseconds" in rendered
        assert "include_deleted=false" in rendered


# ---------------------------------------------------------------------------
# Use case 8: Multiple skills compose correctly
# Real deployments will have several skills active at once.
# ---------------------------------------------------------------------------

class TestMultiSkillComposition:
    """Verify that multiple domain skills coexist cleanly in one prompt."""

    def test_three_skills_all_present(self, tmp_path, mcp_template):
        from cuga_skills.loader import SkillsManager

        _skill(tmp_path, "01_locale.md", "# EU Locale\n\nUse DD/MM/YYYY dates.")
        _skill(tmp_path, "02_privacy.md", "# Privacy\n\nMask email addresses.")
        _skill(tmp_path, "03_workflow.md", "# Approval Workflow\n\nAlways confirm deletions.")

        skills = SkillsManager.load_from_directory(tmp_path)
        rendered = _render(mcp_template, skills=skills)

        assert "EU Locale" in rendered
        assert "Privacy" in rendered
        assert "Approval Workflow" in rendered

    def test_skills_dont_bleed_into_each_other(self, tmp_path):
        from cuga_skills.loader import SkillsManager

        _skill(tmp_path, "a.md", "# Skill Alpha\n\nAlpha-only guidance: ALPHA_TOKEN.")
        _skill(tmp_path, "b.md", "# Skill Beta\n\nBeta-only guidance: BETA_TOKEN.")

        result = SkillsManager.load_from_directory(tmp_path)

        # Dividers separate the two
        alpha_pos = result.index("ALPHA_TOKEN")
        sep_pos = result.index("---", alpha_pos)
        beta_pos = result.index("BETA_TOKEN")
        assert alpha_pos < sep_pos < beta_pos

    def test_merged_skills_from_two_directories(self, tmp_path):
        from cuga_skills.loader import SkillsManager

        dir_shared = tmp_path / "shared"
        dir_tenant = tmp_path / "tenant"
        dir_shared.mkdir()
        dir_tenant.mkdir()

        _skill(dir_shared, "privacy.md", "# Privacy\n\nMask PII.")
        _skill(dir_tenant, "brand.md", "# Brand Voice\n\nFormal tone only.")

        shared_skills = SkillsManager.load_from_directory(dir_shared)
        tenant_skills = SkillsManager.load_from_directory(dir_tenant)
        merged = SkillsManager.merge(shared_skills, tenant_skills)

        assert "Privacy" in merged
        assert "Brand Voice" in merged
        assert merged.count("# SKILLS") == 1  # single header in merged output

    def test_skills_appear_before_tools_section(self, tmp_path, mcp_template):
        """Skills must be visible to the model before the tools list."""
        from cuga_skills.loader import SkillsManager

        _skill(tmp_path, "any.md", "# Ordering Check\n\nSKILL_MARKER")
        skills = SkillsManager.load_from_directory(tmp_path)

        tool = {
            "name": "some_tool",
            "description": "Does something. TOOL_MARKER",
            "params_str": "",
            "params_doc": "",
            "response_doc": "",
        }
        rendered = mcp_template.render(
            base_prompt=None,
            apps=[],
            allow_user_clarification=True,
            return_to_user_cases=None,
            instructions=None,
            tools=[tool],
            task_loaded_from_file=False,
            is_autonomous_subtask=False,
            enable_find_tools=False,
            special_instructions=None,
            skills=skills,
        )
        assert rendered.index("SKILL_MARKER") < rendered.index("TOOL_MARKER")


# ---------------------------------------------------------------------------
# Use case 9: Skills with rich markdown — code fences, tables, nested lists
# Verify the manager doesn't mangle complex markdown content.
# ---------------------------------------------------------------------------

class TestRichMarkdownInSkills:
    """Skills can contain code blocks, tables, and nested lists without corruption."""

    SKILL_WITH_CODE = """\
# Error Recovery Pattern

When an API call returns a 429 (rate limit), use this retry pattern:

```python
import asyncio

for attempt in range(3):
    try:
        result = await api_call()
        break
    except RateLimitError:
        await asyncio.sleep(2 ** attempt)
```

Never retry more than 3 times.
"""

    SKILL_WITH_TABLE = """\
# Status Code Meanings

| Code | Meaning          | Action                     |
|------|------------------|----------------------------|
| 200  | Success          | Continue                   |
| 404  | Not found        | Inform user, stop          |
| 429  | Rate limited     | Wait and retry (max 3×)    |
| 500  | Server error     | Retry once, then escalate  |
"""

    SKILL_WITH_NESTED_LIST = """\
# Output Validation Rules

Before returning any final answer:

- **Check completeness**
  - All requested fields are present
  - No `None` or `null` values in required fields
- **Check consistency**
  - Dates are in the required format
  - Currency amounts match the requested currency
  - IDs cross-reference correctly between systems
"""

    def test_code_block_preserved(self, tmp_path):
        _skill(tmp_path, "retry.md", self.SKILL_WITH_CODE)
        result = _load(tmp_path)
        assert "```python" in result
        assert "asyncio.sleep" in result
        assert "RateLimitError" in result

    def test_table_preserved(self, tmp_path):
        _skill(tmp_path, "status_codes.md", self.SKILL_WITH_TABLE)
        result = _load(tmp_path)
        assert "Rate limited" in result
        assert "429" in result

    def test_nested_list_preserved(self, tmp_path):
        _skill(tmp_path, "validation.md", self.SKILL_WITH_NESTED_LIST)
        result = _load(tmp_path)
        assert "Check completeness" in result
        assert "No `None` or `null` values" in result

    def test_code_block_reaches_rendered_prompt(self, tmp_path, mcp_template):
        _skill(tmp_path, "retry.md", self.SKILL_WITH_CODE)
        skills = _load(tmp_path)
        rendered = _render(mcp_template, skills=skills)
        assert "asyncio.sleep" in rendered
        assert "RateLimitError" in rendered


# ---------------------------------------------------------------------------
# Use case 10: Unicode and international content
# Non-ASCII skill content must survive loading unchanged.
# ---------------------------------------------------------------------------

class TestUnicodeSkills:
    SKILL_JP = "# 日本語スキル\n\nすべての応答は日本語でお願いします。"
    SKILL_EMOJI = "# Tone Marker\n\nPrefx every answer with ✅ when the task succeeds, ❌ when it fails."
    SKILL_ARABIC = "# مهارة العربية\n\nالرد دائماً باللغة العربية إذا كتب المستخدم بالعربية."

    def test_japanese_skill_loads(self, tmp_path):
        _skill(tmp_path, "jp.md", self.SKILL_JP)
        result = _load(tmp_path)
        assert "日本語スキル" in result
        assert "すべての応答" in result

    def test_emoji_skill_preserved(self, tmp_path, mcp_template):
        _skill(tmp_path, "emoji.md", self.SKILL_EMOJI)
        skills = _load(tmp_path)
        rendered = _render(mcp_template, skills=skills)
        assert "✅" in rendered
        assert "❌" in rendered

    def test_arabic_skill_loads(self, tmp_path):
        _skill(tmp_path, "ar.md", self.SKILL_ARABIC)
        result = _load(tmp_path)
        assert "مهارة العربية" in result


# ---------------------------------------------------------------------------
# Use case 11: Skills interact correctly with special_instructions (policies)
# Both must coexist cleanly — policies don't clobber skills and vice versa.
# ---------------------------------------------------------------------------

class TestSkillsAndPoliciesCoexist:
    """
    special_instructions carries policy content (set via env var CUGA_POLICIES_CONTENT).
    Skills are a separate, additive layer.
    """

    def test_both_present_in_prompt(self, tmp_path, mcp_template):
        from cuga_skills.loader import SkillsManager

        _skill(tmp_path, "skill.md", "# My Skill\n\nSKILL_GUIDANCE")
        skills = SkillsManager.load_from_directory(tmp_path)
        rendered = _render(mcp_template, skills=skills, special_instructions="POLICY_CONTENT")

        assert "SKILL_GUIDANCE" in rendered
        assert "POLICY_CONTENT" in rendered

    def test_policy_before_skills(self, tmp_path, mcp_template):
        """special_instructions renders before skills in the template."""
        from cuga_skills.loader import SkillsManager

        _skill(tmp_path, "skill.md", "# My Skill\n\nSKILL_CONTENT")
        skills = SkillsManager.load_from_directory(tmp_path)
        rendered = _render(mcp_template, skills=skills, special_instructions="POLICY_BLOCK")

        assert rendered.index("POLICY_BLOCK") < rendered.index("SKILL_CONTENT")

    def test_no_policy_skills_still_render(self, tmp_path, mcp_template):
        from cuga_skills.loader import SkillsManager

        _skill(tmp_path, "skill.md", "# Solo Skill\n\nSOLO_GUIDANCE")
        skills = SkillsManager.load_from_directory(tmp_path)
        rendered = _render(mcp_template, skills=skills, special_instructions=None)

        assert "SOLO_GUIDANCE" in rendered

    def test_no_skills_policy_still_renders(self, mcp_template):
        rendered = _render(mcp_template, skills=None, special_instructions="POLICY_ONLY")
        assert "POLICY_ONLY" in rendered
        assert "# SKILLS" not in rendered


# ---------------------------------------------------------------------------
# Use case 12: Zero-config auto-discovery via .cuga/skills/
# ---------------------------------------------------------------------------

class TestAutoDiscoveryPattern:
    """
    The recommended pattern for teams: drop skill files into .cuga/skills/
    and they are loaded automatically without touching any Python code.
    """

    def test_auto_discovery_from_cuga_folder(self, tmp_path):
        from cuga_skills.loader import SkillsManager

        skills_dir = tmp_path / ".cuga" / "skills"
        skills_dir.mkdir(parents=True)
        _skill(skills_dir, "ops_rules.md", "# Ops Rules\n\nNever deploy on Fridays.")

        # Simulate what CugaAgent does internally
        skills = SkillsManager.load_from_cuga_folder(tmp_path / ".cuga")
        assert "Ops Rules" in skills
        assert "Never deploy on Fridays." in skills

    def test_no_skills_dir_gracefully_returns_empty(self, tmp_path):
        from cuga_skills.loader import SkillsManager

        (tmp_path / ".cuga").mkdir()
        # .cuga/skills/ does NOT exist
        skills = SkillsManager.load_from_cuga_folder(tmp_path / ".cuga")
        assert skills == ""

    def test_empty_skills_dir_gracefully_returns_empty(self, tmp_path):
        from cuga_skills.loader import SkillsManager

        skills_dir = tmp_path / ".cuga" / "skills"
        skills_dir.mkdir(parents=True)
        # Directory exists but has no .md files
        skills = SkillsManager.load_from_cuga_folder(tmp_path / ".cuga")
        assert skills == ""

    def test_cuga_agent_loads_skills_via_plugin(self, tmp_path):
        """
        CugaAgent loads skills via a CugaSkillsPlugin passed to the plugins list.
        The plugin auto-discovers .cuga/skills/ from the cuga_folder context.
        """
        from unittest.mock import MagicMock, patch
        from cuga.sdk import CugaAgent
        from cuga_skills import CugaSkillsPlugin

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        _skill(skills_dir, "brand.md", "# Brand\n\nPLUGIN_LOADED_SKILL_CONTENT")

        plugin = CugaSkillsPlugin(skills_dir=str(skills_dir))

        with patch("cuga.sdk.LLMManager") as mock_llm_manager:
            mock_llm_manager.return_value.get_model.return_value = MagicMock()
            agent = CugaAgent(plugins=[plugin], auto_load_policies=False)

        assert "PLUGIN_LOADED_SKILL_CONTENT" in agent._skills

    def test_cuga_agent_multiple_plugins_merge_skills(self, tmp_path):
        """
        Multiple CugaSkillsPlugin instances each contribute their skills;
        all contributions appear in agent._skills.
        """
        from unittest.mock import MagicMock, patch
        from cuga.sdk import CugaAgent
        from cuga_skills import CugaSkillsPlugin

        dir_a = tmp_path / "shared"
        dir_b = tmp_path / "tenant"
        dir_a.mkdir(); dir_b.mkdir()
        _skill(dir_a, "shared.md", "# Shared\n\nSHARED_CONTENT")
        _skill(dir_b, "tenant.md", "# Tenant\n\nTENANT_CONTENT")

        plugins = [
            CugaSkillsPlugin(skills_dir=str(dir_a)),
            CugaSkillsPlugin(skills_dir=str(dir_b)),
        ]

        with patch("cuga.sdk.LLMManager") as mock_llm_manager:
            mock_llm_manager.return_value.get_model.return_value = MagicMock()
            agent = CugaAgent(plugins=plugins, auto_load_policies=False)

        assert "SHARED_CONTENT" in agent._skills
        assert "TENANT_CONTENT" in agent._skills

    def test_cuga_agent_no_plugins_has_empty_skills(self):
        """
        CugaAgent with no plugins results in empty _skills string.
        """
        from unittest.mock import MagicMock, patch
        from cuga.sdk import CugaAgent

        with patch("cuga.sdk.LLMManager") as mock_llm_manager:
            mock_llm_manager.return_value.get_model.return_value = MagicMock()
            agent = CugaAgent(auto_load_policies=False)

        assert agent._skills == ""


# ---------------------------------------------------------------------------
# Use case 9: Tool registration via plugins
# Plugins that implement on_tools_build can contribute LangChain tools at
# agent init time, alongside (or instead of) prompt contributions.
# ---------------------------------------------------------------------------

class TestToolRegistrationPlugin:

    def test_tool_plugin_registers_tools(self, tmp_path):
        """
        A plugin with on_tools_build has its tools added to the tool_provider.
        """
        from unittest.mock import MagicMock, patch
        from langchain_core.tools import tool
        from cuga.sdk import CugaAgent
        from cuga_plugin_sdk import ToolContribution

        @tool
        def fake_calculator(x: int, y: int) -> int:
            """Add two numbers."""
            return x + y

        class CalculatorPlugin:
            name = "calculator-plugin"
            version = "0.1.0"
            def on_prompt_build(self, context):
                return None
            def on_tools_build(self, context):
                return ToolContribution(tools=[fake_calculator])

        with patch("cuga.sdk.LLMManager") as mock_llm:
            mock_llm.return_value.get_model.return_value = MagicMock()
            agent = CugaAgent(plugins=[CalculatorPlugin()], auto_load_policies=False)

        registered = [t.name for t in agent.tool_provider.tools]
        assert "fake_calculator" in registered

    def test_tool_plugin_tools_visible_to_subsequent_plugins(self, tmp_path):
        """
        Tools registered by plugin N are visible in existing_tools for plugin N+1.
        """
        from unittest.mock import MagicMock, patch
        from langchain_core.tools import tool
        from cuga.sdk import CugaAgent
        from cuga_plugin_sdk import ToolContribution

        @tool
        def tool_alpha(x: str) -> str:
            """Tool alpha."""
            return x

        seen_by_second = {}

        class FirstPlugin:
            name = "first"
            version = "0.1.0"
            def on_prompt_build(self, context): return None
            def on_tools_build(self, context):
                return ToolContribution(tools=[tool_alpha])

        class SecondPlugin:
            name = "second"
            version = "0.1.0"
            def on_prompt_build(self, context): return None
            def on_tools_build(self, context):
                seen_by_second["tools"] = list(context.existing_tools)
                return None

        with patch("cuga.sdk.LLMManager") as mock_llm:
            mock_llm.return_value.get_model.return_value = MagicMock()
            CugaAgent(plugins=[FirstPlugin(), SecondPlugin()], auto_load_policies=False)

        assert "tool_alpha" in seen_by_second["tools"]

    def test_tool_plugin_returning_none_is_safe(self):
        """
        A plugin whose on_tools_build returns None does not crash the agent.
        """
        from unittest.mock import MagicMock, patch
        from cuga.sdk import CugaAgent

        class OptOutToolPlugin:
            name = "opt-out-tools"
            version = "0.1.0"
            def on_prompt_build(self, context): return None
            def on_tools_build(self, context): return None

        with patch("cuga.sdk.LLMManager") as mock_llm:
            mock_llm.return_value.get_model.return_value = MagicMock()
            agent = CugaAgent(plugins=[OptOutToolPlugin()], auto_load_policies=False)

        assert agent._skills == ""

    def test_dual_plugin_contributes_both_prompt_and_tools(self, tmp_path):
        """
        A single plugin can contribute to both the prompt and the tool registry.
        """
        from unittest.mock import MagicMock, patch
        from langchain_core.tools import tool
        from cuga.sdk import CugaAgent
        from cuga_plugin_sdk import PromptContribution, ToolContribution

        @tool
        def dual_tool(x: str) -> str:
            """A dual tool."""
            return x

        class DualPlugin:
            name = "dual"
            version = "0.1.0"
            def on_prompt_build(self, context):
                return PromptContribution(content="# SKILLS\n\n## Dual Skill\nUse dual_tool.")
            def on_tools_build(self, context):
                return ToolContribution(tools=[dual_tool])

        with patch("cuga.sdk.LLMManager") as mock_llm:
            mock_llm.return_value.get_model.return_value = MagicMock()
            agent = CugaAgent(plugins=[DualPlugin()], auto_load_policies=False)

        assert "Dual Skill" in agent._skills
        registered = [t.name for t in agent.tool_provider.tools]
        assert "dual_tool" in registered

    def test_prompt_only_plugin_is_unaffected_by_tool_hook(self, tmp_path):
        """
        CugaSkillsPlugin (prompt-only) still works correctly — no on_tools_build call.
        """
        from unittest.mock import MagicMock, patch
        from cuga.sdk import CugaAgent
        from cuga_skills.cuga_adapter import CugaSkillsPlugin

        _skill(tmp_path, "greet.md", "# Greeting\n\nAlways greet warmly.")
        plugin = CugaSkillsPlugin(skills_dir=str(tmp_path))

        with patch("cuga.sdk.LLMManager") as mock_llm:
            mock_llm.return_value.get_model.return_value = MagicMock()
            agent = CugaAgent(plugins=[plugin], auto_load_policies=False)

        assert "Greeting" in agent._skills


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
