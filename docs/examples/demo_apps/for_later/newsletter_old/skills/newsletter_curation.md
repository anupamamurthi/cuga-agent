# Newsletter Curation

Applies when asked to curate and compose an AI/ML newsletter digest.

## Input

You receive a list of buffered items in the message, serialised as JSON.
Each item is a dict with these fields:
- `title`     — the article or episode title
- `url`       — the link to the full article or episode page
- `summary`   — a short description (up to 300 chars)
- `source`    — where it came from, e.g. "arxiv.org" or "podcast: Practical AI"
- `published` — publication date string

Podcast items additionally have:
- `transcript_excerpt` — first ~2000 chars of the Whisper transcription
- `duration_minutes`   — episode length in minutes

## Steps

1. **Partition** — separate items into `rss_items` (no `transcript_excerpt`) and
   `podcast_items` (have `transcript_excerpt`).
2. **Deduplicate** — drop items with identical or very similar titles.
   Check your thread memory — skip anything sent in the last 2–3 runs.
3. **Filter** — keep only items relevant to AI, ML, LLMs, agents, or RAG.
4. **Select** — pick 5–10 RSS items and up to 3 podcast items. Prefer
   recent breakthroughs; ensure variety across sections.
5. **Compose** — write a complete HTML email (see template below).
   - Every item must include a clickable link using its `url` field.
   - For podcast items, extract a direct quote from `transcript_excerpt`.
6. **Return** — output only the raw HTML. Do not call any tools.

## Sections

Assign each RSS item to one section:

| Section | What belongs here |
|---|---|
| Research Papers | arXiv preprints, academic work, benchmarks |
| Industry & Products | Launches, releases, company news, funding |
| Tools & Open Source | GitHub repos, HuggingFace models, frameworks |
| Community & Discussion | Hacker News, Reddit, tutorials, opinion |
| On The Radar | Anything mentioning cuga or cuga++ |

Omit a section if it has no items. Never invent items.

## HTML structure

Build the email using this exact structure. Fill in real values from the items —
do not output placeholder text like `{ITEM_TITLE}`.

```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:660px;margin:0 auto;background:#f4f4f8;padding:20px;">

  <div style="background:#1a1a2e;color:#fff;padding:30px 28px;border-radius:8px 8px 0 0;">
    <h1 style="margin:0;font-size:20px;font-weight:700;">📡 AI Digest</h1>
    <p style="margin:6px 0 0;color:#a0a0c0;font-size:13px;">Apr 7 · 8 curated items</p>
  </div>

  <div style="background:#fff;padding:28px;border-radius:0 0 8px 8px;">

    <!-- One <h2> per section, then one <div> per item in that section -->
    <h2 style="font-size:15px;color:#1a1a2e;border-bottom:2px solid #e8e8f0;padding-bottom:6px;margin-top:28px;">Research Papers</h2>

    <!-- RSS item — repeat this block for each item, using its actual field values -->
    <div style="margin-bottom:18px;">
      <a href="ITEM URL HERE" style="font-size:14px;font-weight:600;color:#2563eb;text-decoration:none;">ITEM TITLE HERE</a>
      <p style="margin:4px 0 0;font-size:13px;color:#4b5563;line-height:1.5;">1-2 sentence summary drawn from the item summary field.</p>
      <span style="font-size:11px;color:#9ca3af;">SOURCE · DATE</span>
    </div>

    <!-- Podcast Highlights — only if podcast items exist -->
    <h2 style="font-size:15px;color:#1a1a2e;border-bottom:2px solid #e8e8f0;padding-bottom:6px;margin-top:28px;">🎙️ Podcast Highlights</h2>

    <!-- Podcast item — use this card style -->
    <div style="margin-bottom:18px;background:#f8f9ff;border-left:3px solid #2563eb;padding:14px 16px;border-radius:0 6px 6px 0;">
      <a href="EPISODE URL HERE" style="font-size:14px;font-weight:600;color:#2563eb;text-decoration:none;">EPISODE TITLE HERE</a>
      <p style="margin:4px 0 6px;font-size:13px;color:#4b5563;line-height:1.5;">One sentence about what the episode covers.</p>
      <blockquote style="margin:8px 0 0;padding:8px 12px;background:#fff;border-left:2px solid #a0a0c0;font-size:12px;color:#6b7280;font-style:italic;line-height:1.6;">
        "A direct quote pulled from the transcript_excerpt field."
      </blockquote>
      <span style="font-size:11px;color:#9ca3af;">SOURCE · DURATION min · DATE</span>
    </div>

    <hr style="border:none;border-top:1px solid #e8e8f0;margin:28px 0 16px;">
    <p style="font-size:11px;color:#9ca3af;text-align:center;">Powered by CugaAgent</p>

  </div>
</body>
</html>
```

## Subject line

Format: `AI Digest: [Date] — [one-line teaser of the top story or theme]`

Example: `AI Digest: Apr 7 — ReAct-style agents dominate + Practical AI on tool use`

## Memory across runs

Use your thread memory to:
- Avoid repeating items from the last 2–3 digests.
- Note recurring themes to surface in the subject line.
- Track which podcast episodes have already been featured.
