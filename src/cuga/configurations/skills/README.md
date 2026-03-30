# Skills — Markdown-Driven Agent Guidance

Skills are named markdown files that inject domain-specific guidance into the
agent's system prompt. Inspired by OpenClaw's skills-as-markdown pattern, the
idea is simple: write a `.md` file, drop it in a folder, and the agent picks
it up automatically — no code changes required.

---

## Quick start

```
.cuga/
  skills/
    currency_rules.md   ← your skill file
```

```python
from cuga.sdk import CugaAgent

# Skills in .cuga/skills/ are auto-loaded (zero-config)
agent = CugaAgent(tools=[...])

# Or point to any directory
agent = CugaAgent(tools=[...], skills_dir="./my_skills")
```

That's it. Every skill file in the directory is injected into the system
prompt before the agent processes any user message.

---

## Skill file format

A skill is a plain markdown file. The `# H1` heading is the skill name;
everything below it is the guidance.

```markdown
# Currency Conversion

Applies when the user asks about amounts in a different currency.

## When to use
- User asks to "show amounts in EUR" but the API returns USD
- Any task involving monetary values where a currency mismatch exists

## Guidelines
- Always state the exchange rate source
- Round converted amounts to 2 decimal places
- Show both the original value and the converted value
```

There is no required schema beyond that. Write whatever helps the agent.
Sub-headers, bullet lists, numbered steps, tables, and code blocks all work.

---

## How it works

Skills are loaded once at `CugaAgent` init time from disk, then rendered into
the system prompt on every conversation turn by the `prepare_tools_and_apps`
node — the first node in the CugaLite execution graph.

The prompt layout (in order):

```
[ROLE]
[Connected Applications]
[INSTRUCTIONS]
[special_instructions]   ← policy content (CUGA_POLICIES_CONTENT / Playbooks)
[SKILLS]                 ← your skill files, here
[Critical Rules]
[Available Tools]
[Final Reminder]
```

Skills sit between policy content and the critical execution rules, making
them visible to the model early in the system prompt and ahead of the tool
list.

---

## Loading patterns

### Auto-discovery (recommended)

Drop files in `.cuga/skills/`. Any `CugaAgent` using the default
`cuga_folder=".cuga"` picks them up automatically.

```
.cuga/
  skills/
    privacy.md
    date_formatting.md
    eu_locale.md
```

### Explicit directory

```python
agent = CugaAgent(tools=[...], skills_dir="./domain_skills/acme_corp")
```

When `skills_dir` is set explicitly, it takes precedence over auto-discovery.
`.cuga/skills/` is not loaded.

### Merging multiple directories

```python
from cuga.configurations.skills_manager import SkillsManager

shared = SkillsManager.load_from_directory("./shared_skills")
tenant = SkillsManager.load_from_directory("./tenants/acme/skills")
merged = SkillsManager.merge(shared, tenant)

agent = CugaAgent(tools=[...], special_instructions=merged)
# Or load merged into CugaAgent via a tmp dir if you need the auto-path
```

### Direct use (for inspection)

```python
from cuga.configurations.skills_manager import SkillsManager

skills = SkillsManager.load_from_directory("./my_skills")
print(skills)   # See exactly what gets injected into the prompt
```

---

## Relationship with policies

Skills and policies (`special_instructions`) are **additive** — they both
appear in the system prompt and do not replace each other.

| | Skills | Policies (Playbooks / special_instructions) |
|---|---|---|
| **Format** | Markdown files in a folder | Markdown via `.cuga/*.md` or env var |
| **Scope** | Guidance — the agent interprets and applies judgment | Rules — can be enforced or trigger interrupts |
| **Managed by** | Anyone who can write a file | Policy system (`PoliciesManager`) |
| **Order in prompt** | After `special_instructions` | Before skills |
| **Hard enforcement** | No | Yes (ToolApproval, IntentGuard) |

Use skills for domain knowledge, formatting conventions, workflow guidance, and
tribal knowledge. Use policies when you need hard gates (approval, blocking,
output transformation).

---

## Use cases

### Domain knowledge

```markdown
# CRM ↔ Billing Field Mapping

`customer_id` in the CRM is the same as `account_ref` in Billing.
Always use the CRM ID when cross-referencing between the two systems.
```

### Workflow choreography

```markdown
# Refund Processing Workflow

Always follow this sequence:
1. `get_order(order_id)` — retrieve the order
2. `check_return_eligibility(order_id)` — verify eligibility
3. Only if eligible: `process_refund(order_id, amount)`
4. Confirm with `get_refund_status(refund_id)`
```

### API quirk documentation

```markdown
# Acme CRM Quirks

The `created_at` field is Unix milliseconds, not seconds — divide by 1000
before converting to a human-readable date (despite what the docs say).

The `/contacts` endpoint ignores `total_count: 0` — always paginate until
the `items` array is empty.
```

### Localization / regional rules

```markdown
# EU Market Rules

Dates: DD/MM/YYYY. Currency: € 1.234,56 (period = thousands, comma = decimal).
VAT must be shown separately. Never display net prices without VAT.
```

### Destructive operation soft guards

```markdown
# Confirmation for Destructive Actions

Before calling any tool named `delete_*`, `archive_*`, or `purge_*`, verify
the user's message contains an explicit confirmation: "yes", "confirm", or
"go ahead". If absent, ask for confirmation before proceeding.
```

### Brand voice / tone

```markdown
# ACME Corp Communication Style

Formal tone. No contractions. Numbers: USD with 2 decimal places.
Forbidden phrases: "awesome", "fantastic" — use "confirmed", "completed".
```

### Multi-tenant customisation

Each customer gets their own `.cuga/skills/` folder with their specific rules.
The same agent binary, different behaviour per deployment:

```
tenants/
  acme/
    .cuga/skills/
      brand_voice.md
      field_mapping.md
  globex/
    .cuga/skills/
      eu_locale.md
      vat_rules.md
```

### Staging/environment flags

Inject a skill at deploy time via the CI/CD pipeline:

```markdown
# Staging Environment

⚠️ All data is synthetic. Prefix every answer with "STAGING DATA —
not production-accurate". Never present results as final.
```

### Error recovery patterns

```markdown
# Rate Limit Handling

When a tool raises a 429 error, wait 2 seconds and retry. Maximum 3 retries.
After 3 failures, return to the user with: "The service is rate-limiting
requests. Please try again in a few minutes."
```

### Output contracts

```markdown
# Output Format Contract

All monetary values: include currency code (USD, EUR, GBP).
All lists: ordered by `created_at` descending.
Never truncate lists — paginate to completion before returning.
```

---

## Example skills (bundled)

Three examples are bundled in `src/cuga/configurations/skills/examples/`:

| File | What it demonstrates |
|---|---|
| `currency_conversion.md` | Cross-unit data transformation |
| `data_privacy.md` | PII handling rules |
| `date_formatting.md` | Timestamp normalisation across APIs |

Load them to test the feature end-to-end:

```python
agent = CugaAgent(
    tools=[my_tool],
    model=my_model,
    skills_dir="src/cuga/configurations/skills/examples",
)
```

---

## Testing

```bash
# Unit tests (no LLM required — fast)
uv run pytest tests/unit/test_skills_manager.py -v
uv run pytest tests/unit/test_skills_use_cases.py -v

# Live tests (requires LLM credentials)
uv run pytest tests/integration/test_skills_live.py -v

# Run only the provider you have credentials for
uv run pytest tests/integration/test_skills_live.py -v -m openai
uv run pytest tests/integration/test_skills_live.py -v -m watsonx
uv run pytest tests/integration/test_skills_live.py -v -m rits
uv run pytest tests/integration/test_skills_live.py -v -m litellm
```

### Required environment variables for live tests

| Provider | Variables required |
|---|---|
| OpenAI | `OPENAI_API_KEY` |
| WatsonX | `WATSONX_PROJECT_ID` + `WATSONX_APIKEY` or `WATSONX_TOKEN` |
| RITS | `RITS_API_KEY` + `RITS_URL` |
| LiteLLM | `LITELLM_API_KEY` (or `OPENAI_API_KEY`) + optionally `LITELLM_BASE_URL` |

Providers without credentials are **skipped automatically** — you don't need
to comment anything out.

### Inspect the rendered prompt without a live LLM

```python
from pathlib import Path
from jinja2 import Template
from cuga.configurations.skills_manager import SkillsManager
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import create_mcp_prompt

template = Template(
    Path("src/cuga/backend/cuga_graph/nodes/cuga_lite/prompts/mcp_prompt.jinja2").read_text()
)
skills = SkillsManager.load_from_directory("src/cuga/configurations/skills/examples")
prompt = create_mcp_prompt(tools=[], prompt_template=template, skills=skills)
print(prompt)
```

---

## SkillsManager API reference

```python
from cuga.configurations.skills_manager import SkillsManager

# Load all .md files from a directory (alphabetical order)
skills: str = SkillsManager.load_from_directory("./my_skills")

# Load from the skills/ sub-folder inside a .cuga folder
skills: str = SkillsManager.load_from_cuga_folder(".cuga")

# Merge pre-loaded skills from multiple sources into one string
merged: str = SkillsManager.merge(shared_skills, tenant_skills, extra_skills)
```

All methods return a formatted string or `""` if nothing is found. Errors
(missing files, unreadable files) are logged and silently skipped — the agent
always starts, even if a skill file has a problem.
