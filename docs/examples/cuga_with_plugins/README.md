# cuga_with_plugins

Demonstrates `CugaAgent` with cuga++ plugins:

- **`CugaSkillsPlugin`** — loads `.md` files from `./skills/` and injects a `# SKILLS` section into the system prompt
- **`CatalogPlugin`** — registers product catalog tools (`lookup_product`, `list_products`, `check_inventory`) via `on_tools_build`

## Run

```bash
# OpenAI (default)
uv run python main.py

# Other providers
uv run python main.py --provider anthropic
uv run python main.py --provider litellm --model GCP/gemini-2.0-flash
uv run python main.py --provider rits --model llama-3-3-70b-instruct
uv run python main.py --provider watsonx
```

## Environment variables

| Provider | Required |
|---|---|
| openai | `OPENAI_API_KEY` |
| anthropic | `ANTHROPIC_API_KEY` |
| litellm | `LITELLM_API_KEY`, `LITELLM_BASE_URL` |
| rits | `RITS_API_KEY` |
| watsonx | `WATSONX_APIKEY`, `WATSONX_PROJECT_ID` |
