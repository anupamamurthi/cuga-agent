"""
Engineering Team Collab — Streamlit UI
========================================
Chat-thread style UI showing agents routing messages to each other in real time.
Each message bubble shows who wrote it, what they said, and who they're handing off to.

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
    page_title="Eng Team Collab",
    page_icon="💬",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
st.markdown("""
<style>
body { background: #0d1117; }

.thread-msg {
    border-radius: 12px;
    padding: 14px 18px;
    margin-bottom: 10px;
    border-left: 4px solid #30363d;
    background: #161b22;
}
.thread-msg.pm  { border-color: #58a6ff; }
.thread-msg.dev { border-color: #d29922; }
.thread-msg.qa  { border-color: #3fb950; }
.thread-msg.sre { border-color: #bc8cff; }
.thread-msg.em  { border-color: #f78166; }

.msg-header {
    font-size: 0.85rem;
    font-weight: 700;
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.round-badge {
    background: #21262d;
    color: #8b949e;
    border-radius: 6px;
    padding: 1px 7px;
    font-size: 0.72rem;
    font-weight: 400;
}
.handoff-arrow {
    font-size: 0.78rem;
    color: #8b949e;
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid #21262d;
}
.complete-banner {
    background: #0a3d0a;
    border: 1px solid #3fb950;
    border-radius: 10px;
    padding: 16px 20px;
    color: #3fb950;
    font-weight: 600;
    font-size: 1rem;
    margin-top: 12px;
}
.routing-map {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 12px;
    font-size: 0.8rem;
    color: #8b949e;
}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROVIDERS = ["rits", "openai", "anthropic", "litellm", "watsonx", "ollama"]
DEFAULT_MODELS = {
    "openai":    "gpt-4.1",
    "anthropic": "claude-sonnet-4-6",
    "litellm":   "GCP/gemini-2.0-flash",
    "rits":      "llama-3-3-70b-instruct",
    "watsonx":   "openai/gpt-oss-120b",
    "ollama":    "llama3.1:8b",
}
ENV_VARS = {
    "openai":    "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "litellm":   "LITELLM_API_KEY",
    "rits":      "RITS_API_KEY",
    "watsonx":   "WATSONX_APIKEY",
    "ollama":    "OLLAMA_BASE_URL",
}

AGENT_META = {
    "pm_agent":  {"emoji": "📋", "label": "PM",  "css": "pm",  "color": "#58a6ff"},
    "dev_agent": {"emoji": "💻", "label": "Dev", "css": "dev", "color": "#d29922"},
    "qa_agent":  {"emoji": "🧪", "label": "QA",  "css": "qa",  "color": "#3fb950"},
    "sre_agent": {"emoji": "🔧", "label": "SRE", "css": "sre", "color": "#bc8cff"},
    "em_agent":  {"emoji": "🎯", "label": "EM",  "css": "em",  "color": "#f78166"},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


@st.cache_resource(show_spinner="Building engineering team…")
def _build_agents(provider: str, model: str, api_key: str, api_base: str):
    from cuga.sdk import CugaAgent
    from cuga_runtime.llm import create_llm
    from cuga_skills import CugaSkillsPlugin
    from main import PMPlugin, DevPlugin, QAPlugin, SREPlugin, EMPlugin

    kw = {}
    if api_key:  kw["api_key"]        = api_key
    if api_base: kw["api_base"]       = api_base; kw["ollama_base_url"] = api_base

    llm = create_llm(provider=provider, model=model or None, **kw)

    def sp(role): return CugaSkillsPlugin(skills_dir=str(SKILLS_DIR / role))

    agents = {
        "pm_agent":  CugaAgent(special_instructions="You are the PM in a collaborative engineering team thread.",
                               model=llm, plugins=[sp("pm"),  PMPlugin()],  auto_load_policies=False),
        "dev_agent": CugaAgent(special_instructions="You are the Senior Dev in a collaborative engineering team thread.",
                               model=llm, plugins=[sp("dev"), DevPlugin()], auto_load_policies=False),
        "qa_agent":  CugaAgent(special_instructions="You are the QA Engineer in a collaborative engineering team thread.",
                               model=llm, plugins=[sp("qa"),  QAPlugin()],  auto_load_policies=False),
        "sre_agent": CugaAgent(special_instructions="You are the SRE in a collaborative engineering team thread.",
                               model=llm, plugins=[sp("sre"), SREPlugin()], auto_load_policies=False),
        "em_agent":  CugaAgent(special_instructions="You are the Engineering Manager in a collaborative engineering team thread.",
                               model=llm, plugins=[sp("em"),  EMPlugin()],  auto_load_policies=False),
    }

    for a in agents.values():
        _run_async(a.initialize())

    return agents


def _render_turn(turn):
    """Render a single conversation turn as a styled message bubble."""
    meta     = AGENT_META.get(turn.agent, {"emoji": "?", "label": turn.agent, "css": "em", "color": "#fff"})
    next_meta = AGENT_META.get(turn.handoff_to, None)

    handoff_html = ""
    if turn.is_complete:
        handoff_html = '<div class="handoff-arrow">✅ Pipeline complete</div>'
    elif next_meta:
        handoff_html = (
            f'<div class="handoff-arrow">'
            f'↳ handing off to {next_meta["emoji"]} <strong>{next_meta["label"]}</strong>'
            f'</div>'
        )

    st.markdown(f"""
<div class="thread-msg {meta['css']}">
  <div class="msg-header">
    {meta['emoji']} <span style="color:{meta['color']}">{meta['label']}</span>
    <span class="round-badge">round {turn.round}</span>
  </div>
  <div style="font-size:0.88rem; color:#c9d1d9; white-space:pre-wrap;">{turn.message}</div>
  {handoff_html}
</div>
""", unsafe_allow_html=True)

    if turn.tool_calls:
        with st.expander(f"🔧 {len(turn.tool_calls)} tool call(s) — {meta['label']}", expanded=False):
            for tc in turn.tool_calls:
                st.markdown(f"**`{tc['name']}`**")
                st.json({"input": tc.get("arguments", {}), "output": tc.get("result", "")})


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
for k, v in {"agent_key": None, "turns": [], "running": False, "done": False,
              "thread_id": str(uuid.uuid4())}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("💬 Eng Team Collab")
    st.caption("Agents route work to each other — no fixed order")
    st.divider()

    provider  = st.selectbox("Provider", PROVIDERS)
    model_val = st.text_input("Model override", placeholder=DEFAULT_MODELS[provider])
    api_key   = st.text_input("API key", type="password",
                               placeholder=f"or set {ENV_VARS[provider]}")
    api_base  = ""
    if provider in ("litellm", "ollama"):
        api_base = st.text_input("API base URL")

    st.divider()
    apply = st.button("Apply & Build Team", type="primary", use_container_width=True)

    agent_key = (provider, model_val, api_key, api_base)

    if apply:
        st.session_state.agent_key  = agent_key
        st.session_state.turns      = []
        st.session_state.done       = False
        st.session_state.thread_id  = str(uuid.uuid4())
        try:
            _build_agents(*agent_key)
            st.rerun()
        except Exception as e:
            st.error(f"Failed: {e}")

    if st.session_state.agent_key == agent_key and agent_key[0]:
        st.success("Team ready")
        st.divider()
        st.markdown("**Routing map**")
        st.markdown("""
<div class="routing-map">
📋 PM → 💻 Dev → 🧪 QA<br>
🧪 QA → 💻 Dev <em>(if bugs)</em><br>
🧪 QA → 🔧 SRE <em>(if approved)</em><br>
🔧 SRE → 🎯 EM <em>(risk decision)</em><br>
🎯 EM → any <em>(arbitrate)</em><br>
🎯 EM → ✅ COMPLETE
</div>
""", unsafe_allow_html=True)
        st.divider()
        st.markdown("**Skills per agent**")
        for role, meta in AGENT_META.items():
            skill_file = list((SKILLS_DIR / role.replace("_agent", "")).glob("*.md"))
            for f in skill_file:
                st.markdown(f"{meta['emoji']} `{f.stem}`")

    st.divider()
    if st.button("🗑 Reset", use_container_width=True):
        st.session_state.turns     = []
        st.session_state.done      = False
        st.session_state.thread_id = str(uuid.uuid4())
        st.rerun()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
st.title("💬 Engineering Team — Collaborative Pipeline")
st.caption("Agents talk back and forth. QA can bounce bugs to Dev. SRE can escalate to EM. No fixed order.")

# Feature input
feature = st.text_area(
    "Feature request",
    value="Add rate limiting to the public REST API",
    height=75,
)

col1, col2 = st.columns([2, 1])
with col1:
    run_btn = st.button(
        "🚀 Start Collaboration",
        type="primary",
        disabled=(st.session_state.agent_key != agent_key or st.session_state.agent_key is None),
    )
with col2:
    max_rounds = st.slider("Max rounds", min_value=4, max_value=16, value=10)

if st.session_state.agent_key != agent_key:
    st.info("Press **Apply & Build Team** in the sidebar first.")

st.divider()

# Render existing turns
thread_placeholder = st.container()
with thread_placeholder:
    for turn in st.session_state.turns:
        _render_turn(turn)

    if st.session_state.done:
        last = st.session_state.turns[-1] if st.session_state.turns else None
        if last and last.is_complete:
            st.markdown(
                '<div class="complete-banner">✅ Pipeline complete — all agents signed off.</div>',
                unsafe_allow_html=True,
            )

# ---------------------------------------------------------------------------
# Run pipeline
# ---------------------------------------------------------------------------
if run_btn and not st.session_state.done:
    agents = _build_agents(*st.session_state.agent_key)

    from orchestrator import CollabOrchestrator, Turn

    orch = CollabOrchestrator(agents=agents, max_rounds=max_rounds)

    # Run round by round, rendering each turn as it completes
    status_box = st.empty()

    async def run_pipeline():
        turns_so_far = []

        async def on_turn(turn: Turn):
            turns_so_far.append(turn)
            st.session_state.turns = list(turns_so_far)

        await orch.run(feature=feature, on_turn=on_turn)
        return turns_so_far

    with st.spinner("Collaboration in progress…"):
        final_turns = _run_async(run_pipeline())

    st.session_state.turns = final_turns
    st.session_state.done  = True
    status_box.empty()
    st.rerun()
