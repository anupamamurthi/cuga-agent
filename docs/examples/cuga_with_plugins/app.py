"""
Streamlit UI for CugaAgent + cuga++ plugins demo.

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
# Path setup — let local src packages resolve without installing globally
# ---------------------------------------------------------------------------
EXAMPLE_DIR = Path(__file__).parent
REPO_ROOT   = EXAMPLE_DIR.parent.parent.parent          # cuga-agent-mar30/
CUGAPP_ROOT = REPO_ROOT.parent.parent / "Desktop/cuga++"

for _p in [
    CUGAPP_ROOT / "packages/cuga-plugin-sdk/src",
    CUGAPP_ROOT / "packages/cuga-skills/src",
    CUGAPP_ROOT / "packages/cuga-runtime/src",
]:
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

os.environ.setdefault("DYNA_CONF_ADVANCED_FEATURES__MODE", "api")
os.environ.setdefault("DYNA_CONF_FEATURES__LOCAL_SANDBOX", "true")

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="CugaAgent · Plugins Demo",
    page_icon="🔌",
    layout="wide",
)

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


def _run_async(coro):
    """Run an async coroutine from synchronous Streamlit context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
    except RuntimeError:
        pass
    return asyncio.run(coro)


@st.cache_resource(show_spinner="Initialising agent…")
def _build_agent(provider: str, model: str, api_key: str, api_base: str):
    """
    Build and cache CugaAgent for the given (provider, model) pair.
    Cached by Streamlit — re-runs only when inputs change.
    """
    from cuga.sdk import CugaAgent
    from cuga_runtime.llm import create_llm
    from cuga_skills import CugaSkillsPlugin
    from main import CatalogPlugin  # local to the example

    kwargs = {}
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base
        kwargs["ollama_base_url"] = api_base

    llm = create_llm(provider=provider, model=model or None, **kwargs)

    skills_plugin  = CugaSkillsPlugin(skills_dir=str(EXAMPLE_DIR / "skills"))
    catalog_plugin = CatalogPlugin()

    agent = CugaAgent(
        model=llm,
        plugins=[skills_plugin, catalog_plugin],
        auto_load_policies=False,
    )
    _run_async(agent.initialize())
    return agent


# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
if "messages"   not in st.session_state: st.session_state.messages   = []
if "thread_id"  not in st.session_state: st.session_state.thread_id  = str(uuid.uuid4())
if "agent_key"  not in st.session_state: st.session_state.agent_key  = None


# ---------------------------------------------------------------------------
# Sidebar — configuration + plugin status
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🔌 Plugin Config")
    st.divider()

    provider  = st.selectbox("Provider", PROVIDERS, index=0)
    model_val = st.text_input("Model override", placeholder=DEFAULT_MODELS[provider])
    api_key   = st.text_input("API key", type="password",
                               placeholder=f"or set {ENV_VARS[provider][0]}")
    api_base  = ""
    if provider in ("litellm", "ollama"):
        api_base = st.text_input("API base URL",
                                  placeholder="https://... (or set env var)")

    build_clicked = st.button("Apply & Initialise", type="primary", use_container_width=True)

    st.divider()

    # ---- Plugin status -----
    agent_key = (provider, model_val, api_key, api_base)

    if st.session_state.agent_key == agent_key and st.session_state.agent_key is not None:
        agent = _build_agent(*agent_key)

        st.markdown("**Skills loaded**")
        if agent._skills:
            skill_files = sorted((EXAMPLE_DIR / "skills").glob("*.md"))
            for f in skill_files:
                st.markdown(f"• `{f.stem}`")
            with st.expander("Preview injected prompt"):
                st.markdown(agent._skills)
        else:
            st.caption("No skills found in ./skills/")

        st.markdown("**Tools registered**")
        for t in agent.tool_provider.tools:
            st.markdown(f"• `{t.name}`")
    else:
        st.info("Press **Apply & Initialise** to load the agent.")

    st.divider()
    if st.button("🗑  Clear conversation", use_container_width=True):
        st.session_state.messages  = []
        st.session_state.thread_id = str(uuid.uuid4())
        st.rerun()


# ---------------------------------------------------------------------------
# Apply button handler
# ---------------------------------------------------------------------------
if build_clicked:
    st.session_state.agent_key  = agent_key
    st.session_state.messages   = []
    st.session_state.thread_id  = str(uuid.uuid4())
    try:
        _build_agent(*agent_key)   # trigger cache build + show spinner
        st.rerun()
    except Exception as e:
        st.error(f"Failed to initialise agent: {e}")


# ---------------------------------------------------------------------------
# Main chat area
# ---------------------------------------------------------------------------
st.title("CugaAgent + cuga++ Plugins")
st.caption(
    "Skills (Markdown → system prompt) · Tool registration (on_tools_build) · "
    "Multi-provider LLM"
)

# Render history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("tool_calls"):
            with st.expander(f"🔧 {len(msg['tool_calls'])} tool call(s)"):
                for tc in msg["tool_calls"]:
                    st.markdown(f"**`{tc['name']}`**")
                    st.json({"input": tc.get("arguments", {}),
                             "output": tc.get("result", "")})

# Input
if prompt := st.chat_input("Ask anything…", disabled=st.session_state.agent_key is None):
    if st.session_state.agent_key is None:
        st.warning("Press **Apply & Initialise** in the sidebar first.")
        st.stop()

    # Show user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Run agent
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                agent  = _build_agent(*st.session_state.agent_key)
                result = _run_async(
                    agent.invoke(
                        prompt,
                        thread_id=st.session_state.thread_id,
                        track_tool_calls=True,
                    )
                )
                answer     = str(result)
                tool_calls = getattr(result, "tool_calls", []) or []
            except Exception as e:
                answer     = f"Error: {e}"
                tool_calls = []

        st.markdown(answer)
        if tool_calls:
            with st.expander(f"🔧 {len(tool_calls)} tool call(s)"):
                for tc in tool_calls:
                    st.markdown(f"**`{tc['name']}`**")
                    st.json({"input": tc.get("arguments", {}),
                             "output": tc.get("result", "")})

    st.session_state.messages.append({
        "role":       "assistant",
        "content":    answer,
        "tool_calls": tool_calls,
    })
