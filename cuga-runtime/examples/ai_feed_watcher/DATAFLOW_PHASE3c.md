# Phase 3c — Streaming Knowledge Pipeline (Process Pillar)

> Phase 3c gives CUGA memory. Without it, every assessment starts from zero — CUGA sees
> the post title, the text, and the prompt, with no awareness of what it assessed yesterday,
> last week, or ever. With it, CUGA can answer: "Have we seen this claim before?
> Is this genuinely new signal, or a reframe of a known technique?"

---

## The Problem Phase 3c Solves

Consider this Anthropic post being assessed:

```
Title:   "Claude now supports parallel tool calls natively"
Text:    "We've updated Claude to support executing multiple tools simultaneously..."
```

Without Phase 3c, CUGA assesses this in isolation. It answers: "Is this relevant to agentic AI? Yes. Signal strength? High."

But what if Import AI covered a very similar capability three weeks ago? And Simon Willison wrote about it two weeks ago? CUGA doesn't know. It has no memory. It rates every post on its own merits, without context of prior coverage. "High signal" when it's actually the third article on the same topic this month.

Phase 3c fixes this by continuously feeding CUGA's vector store with everything it has assessed. When a new post arrives, CUGA retrieves similar past assessments before answering.

---

## Architecture

```
╔══════════════════════════════════════════════════════════════════════════╗
║  14 RSS Feeds  →  Feed Watcher  →  cuga.tasks.ai_feed  →  Workers  →  CUGA
║                                                                           ║
║  ALSO (new in 3c):                                                        ║
║  Feed Watcher publishes full post content to:                             ║
║    enterprise.docs.ai_feed  (key=source, value=PostContentEvent)         ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  StreamingKnowledgePipeline  (new process, subscribes to enterprise.docs.ai_feed)
║                                                                           ║
║  for each PostContentEvent:                                               ║
║    1. chunk post content into ~500 token segments                        ║
║    2. generate embedding for each chunk  (e.g., text-embedding-3-small)  ║
║    3. upsert into vector store                                            ║
║       { chunk_text, embedding, post_id, source, title, posted_at }      ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │  upsert
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  Vector Store  (e.g., MongoDB Atlas, Qdrant, pgvector)                   ║
║                                                                           ║
║  Always-current index of past blog post content                          ║
║  Updated continuously as new posts are processed                         ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │  semantic search at query time
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  CUGA's  knowledge_search  tool                                           ║
║                                                                           ║
║  When assessing a new post, CUGA can call:                               ║
║    knowledge_search("parallel tool calls agentic AI")                    ║
║    → returns top-5 relevant chunks from past posts                       ║
║    → CUGA incorporates prior coverage into its assessment                ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## Two Ways to Run the Pipeline

Phase 3c can be implemented two ways, depending on scale:

| | Python worker (Phase 3c default) | Flink (Phase 3c production) |
|---|---|---|
| Subscribes to | `enterprise.docs.ai_feed` | `enterprise.docs.ai_feed` |
| Chunking | Python (`langchain.text_splitter`) | Flink UDF |
| Embedding | OpenAI API call per chunk | Flink AI Model Inference operator |
| Upsert | Python SDK for vector store | Kafka Connector (sink) |
| Throughput | ~10 posts/sec | ~10,000 posts/sec |
| Latency | seconds | milliseconds |
| Operational overhead | zero (another Python process) | Flink cluster needed |
| When to use | starting out, feed watcher scale | enterprise, multi-source, high volume |

Start with the Python worker. Switch to Flink when throughput or latency becomes a constraint.

---

## The Python Worker (Starting Point)

### What it subscribes to

Feed watcher publishes two events per new post in Phase 3c:

```python
# 1. Task event (same as Phase 3a) — for CUGA to assess
producer.produce(
    topic="cuga.tasks.ai_feed",
    key=p["source"],
    value=json.dumps(CugaTaskEvent(...)),
)

# 2. Content event (new in 3c) — for the knowledge pipeline
producer.produce(
    topic="enterprise.docs.ai_feed",
    key=p["source"],
    value=json.dumps({
        "post_id":   p["post_id"],
        "source":    p["source"],
        "feed_name": p["name"],
        "title":     p["title"],
        "text":      p["text"],              # full content
        "url":       p["url"],
        "posted_at": p["posted_at"],
    }),
)
```

Two topics, same post, different consumers. The task topic drives CUGA assessment. The content topic drives the knowledge pipeline. They run independently.

### The pipeline process

```python
# runtime/knowledge_pipeline.py

class StreamingKnowledgePipeline:
    def __init__(self, vector_store, embedder):
        self.consumer = KafkaConsumer(
            "enterprise.docs.ai_feed",
            group_id="knowledge-pipeline",
        )
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
        )
        self.vector_store = vector_store
        self.embedder = embedder

    def run(self):
        for msg in self.consumer:
            post = json.loads(msg.value)
            chunks = self.splitter.split_text(post["text"])

            for i, chunk in enumerate(chunks):
                embedding = self.embedder.embed(chunk)
                self.vector_store.upsert({
                    "id":        f"{post['post_id']}_{i}",
                    "text":      chunk,
                    "embedding": embedding,
                    "metadata": {
                        "post_id":   post["post_id"],
                        "source":    post["source"],
                        "feed_name": post["feed_name"],
                        "title":     post["title"],
                        "url":       post["url"],
                        "posted_at": post["posted_at"],
                    }
                })

            self.consumer.commit()
```

Run it alongside the other workers:

```bash
python -m cuga_runtime.knowledge_pipeline \
  --kafka-bootstrap localhost:9092 \
  --vector-store-url mongodb+srv://...
```

---

## The Flink Version (Production Scale)

Flink is a distributed stream processing engine. Think of it as "Kafka transforms, at scale, without writing a worker loop."

### What Flink replaces

Instead of one Python process doing: poll → chunk → embed → upsert, Flink runs a parallel pipeline where each stage is a separate operator, scaled independently:

```
enterprise.docs.ai_feed (Kafka source)
         │
         ▼
  ┌─────────────────┐
  │  Parse operator │  deserialize JSON, extract text field
  └────────┬────────┘
           │
           ▼
  ┌─────────────────────────────────────┐
  │  ChunkingOperator                   │  split text → N chunks per post
  │  (parallel: one per Kafka partition)│
  └────────┬────────────────────────────┘
           │  each chunk is a new stream record
           ▼
  ┌──────────────────────────────────────────────┐
  │  AI Model Inference operator                  │  Flink's built-in ML operator
  │  model: text-embedding-3-small               │
  │  batching: 64 chunks per request             │  batches automatically
  │  parallel: N inference threads               │
  └────────┬─────────────────────────────────────┘
           │  each record now has: chunk_text + embedding vector
           ▼
  ┌──────────────────────────────────────────────────────┐
  │  MongoDB Atlas Kafka Sink Connector                   │
  │  (or Qdrant, pgvector, Pinecone — connector exists   │
  │   for all major vector stores)                        │
  └──────────────────────────────────────────────────────┘
           │  upsert
           ▼
     Vector Store
```

### Why Flink instead of more Python workers

**Python worker limitation:** Each worker is sequential — it chunks, then calls the embedding API, waits for the response, then upserts, then moves to the next chunk. At 14 feeds × 5-10 posts per cycle × ~10 chunks per post = 700-1400 embedding API calls per run. At 200ms each: 140-280 seconds of blocking.

**Flink's advantage:**
- AI Model Inference batches chunks across posts (64 per request) → 1/64th the number of API calls
- Each operator stage runs in parallel across partitions
- Backpressure is automatic: if the embedding model slows down, Flink buffers upstream without crashing
- Exactly-once semantics: even if Flink crashes mid-job, no chunk is embedded twice

**The operator boundary you care about:** Flink's AI Model Inference operator is where the embedding model call lives. You configure it like:

```sql
-- Flink SQL (declarative, no worker loop code)
CREATE TABLE ai_feed_chunks (
    post_id     STRING,
    source      STRING,
    title       STRING,
    chunk_text  STRING,
    chunk_index INT,
    posted_at   TIMESTAMP
) WITH (
    'connector' = 'kafka',
    'topic'     = 'enterprise.docs.ai_feed.chunks',
    ...
);

INSERT INTO vector_store_sink
SELECT
    post_id,
    source,
    title,
    chunk_text,
    posted_at,
    ML_PREDICT('text-embedding-3-small', chunk_text) AS embedding
FROM ai_feed_chunks;
```

The `ML_PREDICT` call is Flink's AI Model Inference — it handles batching, parallelism, and retry automatically.

---

## How CUGA Uses the Knowledge Store

This is what Phase 3c actually changes for CUGA. CUGA gains a `knowledge_search` tool:

```python
# In CUGA's tool registry
@tool
def knowledge_search(query: str, limit: int = 5) -> list[dict]:
    """Search past AI blog post assessments for relevant prior coverage."""
    embedding = embedder.embed(query)
    results = vector_store.search(embedding, limit=limit)
    return [
        {
            "title":     r["title"],
            "source":    r["feed_name"],
            "posted_at": r["posted_at"],
            "excerpt":   r["text"],
            "url":       r["url"],
        }
        for r in results
    ]
```

### What changes in CUGA's assessment

**Without Phase 3c (Phase 3b):**
```
CUGA receives:
  feed_name: Anthropic Blog
  title: Claude now supports parallel tool calls natively
  text: We've updated Claude...

CUGA answers:
  relevant: yes
  signal: high
  summary: Claude gains native parallel tool execution capability
```

**With Phase 3c:**
```
CUGA receives same prompt, but now calls knowledge_search first:
  knowledge_search("parallel tool calls Claude agents")

knowledge_search returns:
  1. Import AI (3 weeks ago): "OpenAI released parallel function calling..."
  2. Simon Willison (2 weeks ago): "Parallel tool use now available in GPT-4..."
  3. Interconnects (1 week ago): "The parallel tool calling pattern explained..."

CUGA now answers:
  relevant: yes
  signal: medium
  summary: Claude adds native parallel tool execution — the third major lab to
           ship this capability this month after OpenAI (3 wks ago) and OpenAI-API
           update (2 wks ago). Signal is medium: capability is real but no longer novel.
```

Signal goes from `high` to `medium` because CUGA has context. That's the point.

---

## Data Flow: One Post, Full Phase 3c Path

```
1. APScheduler fires (15 min)
   feed_watcher fetches 14 feeds
   New post found: Anthropic Blog / "Claude parallel tool calls"

2. Feed Watcher produces TWO messages:
   → cuga.tasks.ai_feed        key=anthropic  (assessment task for CUGA)
   → enterprise.docs.ai_feed   key=anthropic  (content for knowledge pipeline)

3. PATHS SPLIT HERE — both happen independently:

   PATH A: Assessment
   CugaKafkaWorker polls cuga.tasks.ai_feed
   → calls CUGA /stream
   → CUGA calls knowledge_search("parallel tool calls")
   → knowledge_search queries vector store
   → returns prior coverage (if any)
   → CUGA produces context-aware answer
   → worker produces to cuga.tasks.results
   → worker commits offset

   PATH B: Knowledge update
   StreamingKnowledgePipeline polls enterprise.docs.ai_feed
   → splits post text into chunks
   → generates embeddings (Python API call or Flink ML_PREDICT)
   → upserts into vector store
   → commits offset

4. NEXT POST on same topic:
   knowledge_search now returns THIS post as prior coverage
   CUGA's assessment of the next post is informed by this one
```

---

## The Timing Question

There's a natural question: does the knowledge pipeline update the vector store before or after CUGA assesses the post?

In Phase 3c, both run concurrently. For the post currently being assessed: the knowledge store won't have it yet (the pipeline is processing it at the same time). But it will have all previous posts.

This is intentional. You don't want CUGA to retrieve the post it's currently assessing as "prior coverage." The pipeline is always slightly behind — which means CUGA's context is everything up to (but not including) the current post. That's exactly right.

```
Timeline:
  t=0:  Feed watcher publishes post to both topics
  t=1:  CugaKafkaWorker starts polling / Knowledge pipeline starts polling
  t=2:  CUGA calls knowledge_search → vector store has posts through t-1, not t=0
  t=3:  CUGA completes assessment
  t=4:  Knowledge pipeline finishes embedding this post
  t=5:  Vector store updated with this post
  t=6:  Next post's CUGA assessment will see this one as prior coverage
```

---

## What the Vector Store Holds Over Time

After one week of running Phase 3c on 14 feeds:

```
Vector store contents (approximate):
  ~14 feeds × 3-5 posts/day × 7 days = ~300-500 posts
  ~500 posts × 10 chunks = ~5,000 embeddings

What CUGA can now answer:
  "Have we seen claims about this technique before?" → yes, with excerpts
  "When did coverage of this topic peak?" → retrieve by posted_at
  "Which feed covers this area most?" → aggregate by source in search results
  "Is this a new development or incremental?" → compare to prior summaries
```

After one month: ~1,500-2,000 posts, ~15,000-20,000 embeddings. Still fast to search (vector search scales to billions of vectors). CUGA's assessments get meaningfully better as the knowledge base grows.

---

## Running Phase 3c

```bash
# 1. Start CUGA (port 7860) — with knowledge_search tool registered

# 2. Start Kafka
docker run -p 9092:9092 apache/kafka:3.7.0

# 3. Create the additional topic
kafka-topics.sh --create \
  --topic enterprise.docs.ai_feed \
  --partitions 3 \
  --replication-factor 1 \
  --bootstrap-server localhost:9092

# 4. Start cuga-runtime Kafka workers (Phase 3a workers, unchanged)
python -m cuga_runtime.kafka_worker --topic cuga.tasks.ai_feed

# 5. Start knowledge pipeline (new in 3c)
VECTOR_STORE_URL=... \
EMBEDDING_MODEL=text-embedding-3-small \
OPENAI_API_KEY=... \
  python -m cuga_runtime.knowledge_pipeline

# 6. Start feed watcher (now publishes to both topics)
python -m examples.ai_feed_watcher.service
```

The knowledge pipeline is a separate process. Everything else from Phase 3a is unchanged.
