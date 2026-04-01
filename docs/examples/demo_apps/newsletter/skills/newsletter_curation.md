# Newsletter Curation

Applies when asked to fetch, curate, and send an AI/ML news newsletter.

## Pipeline

Follow these steps in order every time a newsletter run is triggered:

1. **Fetch** — Call `fetch_rss` once per source URL from the config. Collect all returned items.
2. **Deduplicate** — Drop items with identical or near-identical titles.
3. **Filter** — Keep only items relevant to AI, ML, LLMs, agents, or the configured keywords.
4. **Select** — Choose 10–15 of the most significant and diverse items.
   - Prefer recent breakthroughs over incremental updates.
   - Ensure variety: at least one item per section when possible.
   - Skip items whose titles closely match something sent in a previous run (use your thread memory).
5. **Compose** — Write a styled HTML newsletter using the template below.
6. **Send** — Call `send_email` with the subject and HTML body.

## Sections

Assign each selected item to the most fitting section:

| Section | What belongs here |
|---|---|
| **On The Radar** | Mentions of cuga, cuga-agent, or the cuga++ ecosystem |
| **Research Papers** | arXiv preprints, academic work, benchmarks |
| **Industry & Products** | Launches, releases, company news, funding rounds |
| **Tools & Open Source** | GitHub repos, HuggingFace models, frameworks |
| **Community & Discussion** | Hacker News threads, Reddit, tutorials, opinion pieces |

Omit a section entirely if it has no items. Never invent items.

## HTML Template

Use this structure with inline styles (required for email client compatibility):

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:660px;margin:0 auto;background:#f4f4f8;padding:20px;">

  <!-- Header -->
  <div style="background:#1a1a2e;color:#fff;padding:30px 28px;border-radius:8px 8px 0 0;">
    <h1 style="margin:0;font-size:20px;font-weight:700;">📡 {NEWSLETTER_TITLE}</h1>
    <p style="margin:6px 0 0;color:#a0a0c0;font-size:13px;">{DATE} &middot; {ITEM_COUNT} curated items</p>
  </div>

  <!-- Body -->
  <div style="background:#fff;padding:28px;border-radius:0 0 8px 8px;">

    <!-- Repeat per section -->
    <h2 style="font-size:15px;color:#1a1a2e;border-bottom:2px solid #e8e8f0;padding-bottom:6px;margin-top:28px;">{SECTION_NAME}</h2>

    <!-- Repeat per item -->
    <div style="margin-bottom:18px;">
      <a href="{ITEM_URL}" style="font-size:14px;font-weight:600;color:#2563eb;text-decoration:none;">{ITEM_TITLE}</a>
      <p style="margin:4px 0 0;font-size:13px;color:#4b5563;line-height:1.5;">{ITEM_SUMMARY_1_2_SENTENCES}</p>
      <span style="font-size:11px;color:#9ca3af;">{SOURCE_NAME} &middot; {ITEM_DATE}</span>
    </div>

    <!-- Footer -->
    <hr style="border:none;border-top:1px solid #e8e8f0;margin:28px 0 16px;">
    <p style="font-size:11px;color:#9ca3af;text-align:center;">
      Powered by CugaAgent &middot; {TIMESTAMP}
    </p>

  </div>
</body>
</html>
```

## Subject Line

Format: `{NEWSLETTER_TITLE}: {DATE} — {teaser of top story or theme}`

Example: `AI Weekly Digest: Mar 31 — Chain-of-thought models, 3 new open-source agents`

## Memory Across Runs

Your thread preserves memory between newsletter runs. Use it to:
- Avoid re-sending items already covered in the last 2–3 newsletters.
- Note recurring themes so you can surface them in the subject line ("third reasoning paper this week").
- Track which sources have been quiet or noisy lately.
