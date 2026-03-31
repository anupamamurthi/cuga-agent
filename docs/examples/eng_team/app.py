"""
Engineering Team — Streamlit UI
=================================
Five CUGA agents (PM, Dev, QA, SRE, EM) process a feature request
in a visible pipeline.  Each agent card lights up as it runs, showing
the skills it loaded and the tools it called.

Run:
    uv run streamlit run app.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
EXAMPLE_DIR = Path(__file__).parent
CUGAPP_ROOT = Path.home() / "Desktop/cuga++"

for _p in [
    CUGAPP_ROOT / "packages/cuga-plugin-sdk/src",
    CUGAPP_ROOT / "packages/cuga-skills/src",
    CUGAPP_ROOT / "packages/cuga-runtime/src",
]:
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

os.environ.setdefault("DYNA_CONF_ADVANCED_FEATURES__MODE", "api")
os.environ.setdefault("DYNA_CONF_FEATURES__LOCAL_SANDBOX", "true")

SKILLS_DIR = EXAMPLE_DIR / "skills"

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Eng Team · Multi-Agent",
    page_icon="🏗",
    layout="wide",
)

# ---------------------------------------------------------------------------
# CSS — agent card styling
# ---------------------------------------------------------------------------
st.markdown("""
<style>
.agent-card {
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 12px;
    background: #0d1117;
}
.agent-card.active  { border-color: #58a6ff; background: #0c1929; }
.agent-card.done    { border-color: #3fb950; background: #0a1f0a; }
.agent-card.waiting { border-color: #30363d; opacity: 0.5; }
.agent-header {
    display: flex; align-items: center; gap: 10px;
    font-size: 1.05rem; font-weight: 600; margin-bottom: 8px;
}
.pill {
    display: inline-block;
    border-radius: 12px; padding: 2px 10px;
    font-size: 0.75rem; font-weight: 500;
}
.pill-blue   { background: #1f4068; color: #58a6ff; }
.pill-green  { background: #0a3d0a; color: #3fb950; }
.pill-yellow { background: #3d2a00; color: #d29922; }
.pill-gray   { background: #21262d; color: #8b949e; }
.skill-tag {
    display: inline-block;
    background: #21262d; color: #c9d1d9;
    border-radius: 6px; padding: 2px 8px;
    font-size: 0.72rem; margin: 2px;
}
.final-card {
    border: 2px solid #58a6ff;
    border-radius: 12px;
    padding: 24px;
    background: #0c1929;
    margin-top: 16px;
}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROVIDERS = ["openai", "anthropic", "litellm", "rits", "watsonx", "ollama"]
DEFAULT_MODELS = {
    "openai":    "gpt-4.1",
    "anthropic": "claude-sonnet-4-6",
    "litellm":   "GCP/gemini-2.0-flash",
    "rits":      "llama-3-3-70b-instruct",
    "watsonx":   "openai/gpt-oss-120b",
    "ollama":    "llama3.1:8b",
}
ENV_VARS = {
    "openai":    ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "litellm":   ["LITELLM_API_KEY", "LITELLM_BASE_URL"],
    "rits":      ["RITS_API_KEY"],
    "watsonx":   ["WATSONX_APIKEY", "WATSONX_PROJECT_ID"],
    "ollama":    ["OLLAMA_BASE_URL"],
}

AGENT_META = {
    "pm":  {"emoji": "📋", "title": "PM",  "color": "pill-blue",
            "skills": ["requirements_writing", "user_story_format", "acceptance_criteria"],
            "tools":  ["create_user_story", "define_acceptance_criteria", "estimate_story_points"]},
    "dev": {"emoji": "💻", "title": "Dev", "color": "pill-yellow",
            "skills": ["code_design", "implementation_patterns", "api_design"],
            "tools":  ["design_api_endpoint", "suggest_implementation_approach", "check_existing_patterns"]},
    "qa":  {"emoji": "🧪", "title": "QA",  "color": "pill-green",
            "skills": ["test_case_design", "edge_case_detection", "test_coverage"],
            "tools":  ["generate_test_plan", "identify_edge_cases", "assess_test_coverage"]},
    "sre": {"emoji": "🔧", "title": "SRE", "color": "pill-gray",
            "skills": ["deployment_checklist", "monitoring_setup", "rollback_plan"],
            "tools":  ["generate_deployment_checklist", "suggest_monitoring", "classify_rollback_complexity"]},
}

PIPELINE = ["pm", "dev", "qa", "sre"]


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        pass
    return asyncio.run(coro)


def _skill_tags(skills: list[str]) -> str:
    return "".join(f'<span class="skill-tag">{s}</span>' for s in skills)


# ---------------------------------------------------------------------------
# Agent + supervisor builders (cached per provider/model/key)
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Building engineering team…")
def _build_agents(provider: str, model: str, api_key: str, api_base: str):
    from cuga.sdk import CugaAgent
    from cuga_runtime.llm import create_llm
    from cuga_skills import CugaSkillsPlugin
    from main import (PMToolPlugin, DevToolPlugin, QAToolPlugin, SREToolPlugin,
                      EMSkillsPlugin)

    kw = {}
    if api_key:  kw["api_key"] = api_key
    if api_base: kw["api_base"] = api_base; kw["ollama_base_url"] = api_base

    llm = create_llm(provider=provider, model=model or None, **kw)

    def _sp(role): return CugaSkillsPlugin(skills_dir=str(SKILLS_DIR / role))

    pm  = CugaAgent(special_instructions="You are the PM. Write a user story, ACs, and estimate.",
                    model=llm, plugins=[_sp("pm"),  PMToolPlugin()],  auto_load_policies=False)
    dev = CugaAgent(special_instructions="You are the Senior Dev. Design the API, implementation approach, check patterns.",
                    model=llm, plugins=[_sp("dev"), DevToolPlugin()], auto_load_policies=False)
    qa  = CugaAgent(special_instructions="You are the QA Engineer. Generate a test plan, edge cases, coverage assessment.",
                    model=llm, plugins=[_sp("qa"),  QAToolPlugin()],  auto_load_policies=False)
    sre = CugaAgent(special_instructions="You are the SRE. Produce a deployment checklist, monitoring plan, rollback classification.",
                    model=llm, plugins=[_sp("sre"), SREToolPlugin()], auto_load_policies=False)

    agents = {"pm_agent": pm, "dev_agent": dev, "qa_agent": qa, "sre_agent": sre}

    _run_async(pm.initialize())
    _run_async(dev.initialize())
    _run_async(qa.initialize())
    _run_async(sre.initialize())

    return agents, llm


def _invoke_agent(agent, prompt: str, thread_id: str):
    from cuga.sdk import CugaSupervisor
    if isinstance(agent, CugaSupervisor):
        return _run_async(agent.invoke(prompt, thread_id=thread_id))
    return _run_async(agent.invoke(prompt, thread_id=thread_id, track_tool_calls=True))


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
defaults = {
    "agent_key":  None,
    "results":    {},      # role → InvokeResult
    "pipeline_done": False,
    "thread_id":  str(uuid.uuid4()),
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🏗 Eng Team Config")
    st.divider()

    provider  = st.selectbox("Provider", PROVIDERS)
    model_val = st.text_input("Model override", placeholder=DEFAULT_MODELS[provider])
    api_key   = st.text_input("API key", type="password",
                               placeholder=f"or set {ENV_VARS[provider][0]}")
    api_base  = ""
    if provider in ("litellm", "ollama"):
        api_base = st.text_input("API base URL", placeholder="https://...")

    st.divider()
    apply = st.button("Apply & Build Team", type="primary", use_container_width=True)

    agent_key = (provider, model_val, api_key, api_base)

    if apply:
        st.session_state.agent_key   = agent_key
        st.session_state.results     = {}
        st.session_state.pipeline_done = False
        st.session_state.thread_id   = str(uuid.uuid4())
        try:
            _build_agents(*agent_key)
            st.rerun()
        except Exception as e:
            st.error(f"Failed to build team: {e}")

    if st.session_state.agent_key == agent_key and agent_key[0] is not None:
        st.success("Team ready")
        st.divider()
        st.markdown("**Agents**")
        for role, meta in AGENT_META.items():
            st.markdown(f"{meta['emoji']} **{meta['title']}** — {len(meta['skills'])} skills, {len(meta['tools'])} tools")
        st.markdown("🎯 **EM** — supervisor")

    st.divider()
    if st.button("🗑 Reset", use_container_width=True):
        st.session_state.results     = {}
        st.session_state.pipeline_done = False
        st.session_state.thread_id   = str(uuid.uuid4())
        st.rerun()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
st.title("🏗 Engineering Team · Multi-Agent Pipeline")
st.caption(
    "PM → Dev → QA → SRE → EM · Each agent has 3 skills (Markdown → prompt) "
    "and 2-3 tools · Powered by CugaAgent + CugaSupervisor"
)

# Feature request input
feature = st.text_area(
    "Feature request",
    value="Add rate limiting to the public REST API",
    height=80,
    placeholder="Describe the feature or bug fix…",
)

run_btn = st.button(
    "🚀 Run Pipeline",
    type="primary",
    disabled=(st.session_state.agent_key is None or st.session_state.agent_key != agent_key),
)

if st.session_state.agent_key != agent_key:
    st.info("Press **Apply & Build Team** in the sidebar first.")

st.divider()

# ---------------------------------------------------------------------------
# Pipeline display — always show all 4 agent cards
# ---------------------------------------------------------------------------

def _card_state(role: str) -> str:
    results = st.session_state.results
    roles_done = list(results.keys())
    if role in results:
        return "done"
    if roles_done and PIPELINE.index(role) == len(roles_done):
        return "active"
    return "waiting"


def _render_agent_card(role: str, placeholder):
    meta    = AGENT_META[role]
    state   = _card_state(role)
    result  = st.session_state.results.get(role)

    state_label = {"done": "✅ Done", "active": "⚡ Running…", "waiting": "⏳ Waiting"}[state]
    pill_class  = {"done": "pill-green", "active": "pill-blue", "waiting": "pill-gray"}[state]

    with placeholder.container():
        st.markdown(f"""
<div class="agent-card {state}">
  <div class="agent-header">
    {meta['emoji']} {meta['title']}
    <span class="pill {pill_class}">{state_label}</span>
  </div>
  <div style="margin-bottom:6px; font-size:0.8rem; color:#8b949e;">Skills: {_skill_tags(meta['skills'])}</div>
</div>
""", unsafe_allow_html=True)

        if result:
            answer     = str(result)
            tool_calls = getattr(result, "tool_calls", []) or []

            # Short preview
            preview = answer[:400].replace("\n", " ") + ("…" if len(answer) > 400 else "")
            st.markdown(f"> {preview}")

            cols = st.columns(2)
            with cols[0]:
                with st.expander(f"📄 Full output"):
                    st.markdown(answer)
            with cols[1]:
                if tool_calls:
                    with st.expander(f"🔧 {len(tool_calls)} tool call(s)"):
                        for tc in tool_calls:
                            st.markdown(f"**`{tc['name']}`**")
                            st.json({"input": tc.get("arguments", {}),
                                     "output": tc.get("result", "")})
        elif state == "active":
            st.info("Agent is working…")
        elif state == "waiting":
            st.caption("Will run after previous agents complete.")


# Create placeholders for all 4 agent cards
placeholders = {role: st.empty() for role in PIPELINE}
for role, ph in placeholders.items():
    _render_agent_card(role, ph)

# EM final decision placeholder
st.divider()
em_placeholder   = st.empty()
em_placeholder.markdown(
    '<div class="agent-card waiting"><div class="agent-header">🎯 EM — Engineering Manager '
    '<span class="pill pill-gray">⏳ Waiting</span></div>'
    '<div style="color:#8b949e; font-size:0.85rem;">Synthesises all agent outputs into a ship/hold decision.</div></div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

if run_btn and not st.session_state.pipeline_done:
    if st.session_state.agent_key is None:
        st.warning("Build the team first.")
        st.stop()

    agents, llm = _build_agents(*st.session_state.agent_key)
    tid         = st.session_state.thread_id

    context_so_far = f"Feature request: {feature}\n\n"

    for i, role in enumerate(PIPELINE):
        meta = AGENT_META[role]

        # Mark as active
        placeholders[role].empty()
        _render_agent_card(role, placeholders[role])

        # Build prompt — each agent gets context from previous agents
        agent_prompts = {
            "pm":  f"{context_so_far}You are the PM. Write the user story, acceptance criteria, and story-point estimate for this feature.",
            "dev": f"{context_so_far}You are the Dev. Based on the PM spec above, design the API endpoint, suggest an implementation approach, and check for existing patterns.",
            "qa":  f"{context_so_far}You are the QA Engineer. Based on the spec and implementation design above, generate a test plan, identify edge cases, and assess coverage readiness.",
            "sre": f"{context_so_far}You are the SRE. Based on all the above, produce a deployment checklist, monitoring plan, and rollback complexity classification.",
        }

        agent_name = f"{role}_agent"
        try:
            result = _invoke_agent(agents[agent_name], agent_prompts[role], f"{tid}-{role}")
            st.session_state.results[role] = result
            # Accumulate context for next agent
            context_so_far += f"\n\n--- {meta['title'].upper()} OUTPUT ---\n{str(result)}\n"
        except Exception as e:
            st.error(f"{meta['title']} agent failed: {e}")
            break

        # Re-render updated card
        placeholders[role].empty()
        _render_agent_card(role, placeholders[role])

    # EM synthesis
    if len(st.session_state.results) == 4:
        em_placeholder.markdown(
            '<div class="agent-card active"><div class="agent-header">🎯 EM '
            '<span class="pill pill-blue">⚡ Synthesising…</span></div></div>',
            unsafe_allow_html=True,
        )

        from cuga.sdk import CugaSupervisor
        from main import EMSkillsPlugin

        supervisor = CugaSupervisor(
            agents=agents,
            model=llm,
            description="Engineering Manager — synthesise all agent outputs into a final decision",
        )

        em_prompt = (
            f"{context_so_far}\n\n"
            "You are the Engineering Manager. You have received outputs from PM, Dev, QA, and SRE.\n"
            "Produce a final engineering decision with:\n"
            "1. GO / HOLD recommendation with justification\n"
            "2. Risk score table (use your risk_assessment skill)\n"
            "3. Sprint plan (use your sprint_planning skill)\n"
            "4. Stakeholder update (use your stakeholder_update skill — plain English, 3 bullets max)\n"
        )

        try:
            em_result  = _invoke_agent(supervisor, em_prompt, f"{tid}-em")
            em_answer  = str(em_result)
            em_tools   = getattr(em_result, "tool_calls", []) or []

            em_placeholder.markdown(f"""
<div class="final-card">
  <div class="agent-header" style="font-size:1.1rem;">
    🎯 EM — Engineering Manager
    <span class="pill pill-green">✅ Decision Ready</span>
  </div>
</div>
""", unsafe_allow_html=True)

            st.markdown("### Engineering Manager Decision")
            st.markdown(em_answer)

            if em_tools:
                with st.expander(f"🔧 {len(em_tools)} supervisor tool call(s)"):
                    for tc in em_tools:
                        st.markdown(f"**`{tc['name']}`**")
                        st.json({"input": tc.get("arguments", {}),
                                 "output": tc.get("result", "")})

            st.session_state.pipeline_done = True

        except Exception as e:
            em_placeholder.error(f"EM synthesis failed: {e}")

elif st.session_state.pipeline_done and st.session_state.results:
    # Re-render completed state on page refresh
    for role, ph in placeholders.items():
        ph.empty()
        _render_agent_card(role, ph)
    em_placeholder.success("Pipeline complete — scroll up to see the full EM decision.")
