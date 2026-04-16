"""
RSS feeds to watch for AI/LLM signal.

Each entry has:
  name    — display name shown in the UI
  url     — RSS or Atom feed URL
  source  — short slug used as the trigger name (rss.new_post.{source})
"""

RSS_FEEDS = [
    {
        "name": "OpenAI Blog",
        "url": "https://openai.com/blog/rss.xml",
        "source": "openai",
    },
    {
        "name": "Anthropic Blog",
        "url": "https://www.anthropic.com/rss.xml",
        "source": "anthropic",
    },
    {
        "name": "Google DeepMind",
        "url": "https://deepmind.google/blog/rss.xml",
        "source": "deepmind",
    },
    {
        "name": "Hugging Face Blog",
        "url": "https://huggingface.co/blog/feed.xml",
        "source": "huggingface",
    },
    {
        "name": "The Batch (Andrew Ng)",
        "url": "https://read.deeplearning.ai/the-batch/rss/",
        "source": "the_batch",
    },
    {
        "name": "Lilian Weng",
        "url": "https://lilianweng.github.io/index.xml",
        "source": "lilianweng",
    },
    {
        "name": "Simon Willison",
        "url": "https://simonwillison.net/atom/everything/",
        "source": "simonwillison",
    },
    {
        "name": "Import AI (Jack Clark)",
        "url": "https://jack-clark.net/feed/",
        "source": "import_ai",
    },
    {
        "name": "One Useful Thing (Ethan Mollick)",
        "url": "https://www.oneusefulthing.org/feed",
        "source": "mollick",
    },
    {
        "name": "Interconnects (Nathan Lambert)",
        "url": "https://www.interconnects.ai/feed",
        "source": "interconnects",
    },
    {
        "name": "BAIR Blog",
        "url": "https://bair.berkeley.edu/blog/feed.xml",
        "source": "bair",
    },
    {
        "name": "AI Snake Oil",
        "url": "https://aisnakeoil.substack.com/feed",
        "source": "aisnakeoil",
    },
    {
        "name": "Sebastian Raschka",
        "url": "https://magazine.sebastianraschka.com/feed",
        "source": "raschka",
    },
    {
        "name": "Last Week in AI",
        "url": "https://lastweekin.ai/feed",
        "source": "lastweekinai",
    },
]

# The standing CUGA prompt — same for every post
AI_RELEVANCE_PROMPT = (
    "You are monitoring AI research blogs and newsletters for signal about agentic AI. "
    "Given the post below, decide:\n"
    "1. Is this relevant to agentic AI? (yes/no) — be strict: general LLM benchmarks alone are not agentic AI\n"
    "2. If yes: one-sentence summary of the key insight\n"
    "3. If yes: signal strength (high / medium / low) — high means genuinely new, actionable, or surprising\n"
    "4. If no: one-word reason why not (e.g. politics, personal, benchmark, unrelated)\n\n"
    "Reply in this exact format:\n"
    "relevant: yes|no\n"
    "signal: high|medium|low  (omit if not relevant)\n"
    "summary: <one sentence>  (omit if not relevant)\n"
    "reason: <one word>       (omit if relevant)"
)
