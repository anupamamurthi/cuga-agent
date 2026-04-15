# Phase 3c-1 — Flink: How Kafka and Flink Work Together

> Kafka moves data. Flink transforms data while it moves.
> They are designed to work together: Kafka is the pipe, Flink is the processor inside the pipe.

---

## The Mental Model

Think of Kafka as a highway and Flink as a toll booth with a computer inside it.

- Cars (events) travel the highway (Kafka topics) continuously
- The toll booth (Flink) reads each car, does something to it (enrichment, transformation, ML inference), and sends it forward to the next highway (another Kafka topic or a database)
- The cars don't stop moving. The toll booth doesn't batch them up and wait. It processes them as they arrive.

```
Kafka topic A  ──►  Flink job  ──►  Kafka topic B
                        │
                        └──►  Vector Store (MongoDB, Qdrant, etc.)
```

Without Flink, you'd write a Python worker to read from topic A, do the transformation, and write to topic B or the vector store. Flink is what that Python worker becomes when you need it to handle 10,000 events per second instead of 10.

---

## Where Kafka and Flink Live in Phase 3c-1

```
╔══════════════════════════════════════════════════════════════════════════╗
║  Feed Watcher  (port 8002)                                                ║
║                                                                           ║
║  For each new post:                                                       ║
║    producer.produce("enterprise.docs.ai_feed", value=PostContentEvent)   ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  Kafka  —  topic: enterprise.docs.ai_feed                                 ║
║                                                                           ║
║  Kafka's job here: hold the post content events, deliver to Flink        ║
║  Kafka does NOT know about chunking, embeddings, or AI                   ║
║  It just stores and delivers                                              ║
╚══════════════════════════╤═══════════════════════════════════════════════╝
                           │  Flink reads from here (Kafka source connector)
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  Flink Job  (the pipeline)                                                ║
║                                                                           ║
║  Operator 1: KafkaSource                                                  ║
║    reads from enterprise.docs.ai_feed                                    ║
║    deserializes JSON → PostContentEvent struct                           ║
║                                                                           ║
║  Operator 2: FlatMapOperator (chunking)                                   ║
║    splits post["text"] into 500-token chunks                             ║
║    1 PostContentEvent  →  N ChunkEvents                                  ║
║                                                                           ║
║  Operator 3: AI Model Inference (embedding)                               ║
║    batches 64 ChunkEvents together                                       ║
║    calls embedding model once per batch                                  ║
║    1 ChunkEvent  →  1 ChunkWithEmbeddingEvent                            ║
║                                                                           ║
║  Operator 4: MongoDBSink (or Qdrant, pgvector)                           ║
║    upserts each ChunkWithEmbeddingEvent into vector store                ║
╚══════════════════════════════════════════════════════════════════════════╝
                           │
                           ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  Vector Store  (MongoDB Atlas / Qdrant / pgvector)                        ║
║                                                                           ║
║  Always-current. CUGA's knowledge_search tool queries this.              ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## What Kafka Does vs What Flink Does

This is the key distinction:

| | Kafka | Flink |
|---|---|---|
| **Role** | Store and deliver events | Transform events |
| **State** | The events themselves (immutable log) | Intermediate computation state |
| **Guarantee** | Events are not lost, delivered in order per partition | Processing happens exactly once |
| **Analogy** | A conveyor belt | A machine on the conveyor belt |
| **Knows about the data?** | No — Kafka sees bytes | Yes — Flink deserializes and processes |
| **In Phase 3c-1** | Holds `enterprise.docs.ai_feed` events | Chunks them, embeds them, writes to vector store |

Kafka and Flink connect through **Kafka connectors** — Flink has built-in source and sink connectors for Kafka. You declare "read from this topic" and "write to that topic" in your Flink job. Flink handles offset management just like the Python `CugaKafkaWorker` does — it commits offsets to Kafka only after it has successfully processed a batch.

---

## The Flink Job, Step by Step

### Step 1 — KafkaSource: Flink reads from Kafka

```java
// Flink reads enterprise.docs.ai_feed exactly like the Python worker does
// but Flink manages the offsets itself (stored in Flink's checkpoint state)
KafkaSource<PostContentEvent> source = KafkaSource.<PostContentEvent>builder()
    .setBootstrapServers("localhost:9092")
    .setTopics("enterprise.docs.ai_feed")
    .setGroupId("flink-knowledge-pipeline")
    .setValueOnlyDeserializer(new PostContentEventDeserializer())
    .build();

DataStream<PostContentEvent> posts = env.fromSource(
    source,
    WatermarkStrategy.noWatermarks(),
    "Kafka: enterprise.docs.ai_feed"
);
```

At this point Flink has a stream of `PostContentEvent` objects. Same data the Python worker consumes. The difference is Flink can process this stream across many parallel instances — one per Kafka partition.

---

### Step 2 — FlatMap: One post becomes many chunks

A `map` operator is 1-to-1. A `flatMap` is 1-to-many. One post becomes N chunks.

```java
DataStream<ChunkEvent> chunks = posts.flatMap(
    (PostContentEvent post, Collector<ChunkEvent> out) -> {
        List<String> segments = TextChunker.chunk(post.text, 500, 50);
        // 500 tokens per chunk, 50 token overlap
        for (int i = 0; i < segments.size(); i++) {
            out.collect(new ChunkEvent(
                post.postId + "_" + i,
                post.postId,
                post.source,
                post.feedName,
                post.title,
                post.url,
                post.postedAt,
                segments.get(i),   // the chunk text
                i                  // chunk index
            ));
        }
    }
);
```

A 2,000-word Anthropic blog post might produce 8 chunks. Each chunk is now an independent stream record. Flink processes all 8 independently and in parallel.

---

### Step 3 — AI Model Inference: Flink calls the embedding model

This is Flink's built-in ML operator. You tell it: "for each record in this stream, call this model."

```java
// Flink AsyncIO pattern for external API calls
DataStream<ChunkWithEmbeddingEvent> embedded = AsyncDataStream.unorderedWait(
    chunks,
    new EmbeddingFunction(),   // calls text-embedding-3-small API
    30, TimeUnit.SECONDS,      // timeout per call
    100                        // max concurrent in-flight requests
);
```

The `EmbeddingFunction` batches chunks before sending:

```java
class EmbeddingFunction extends RichAsyncFunction<ChunkEvent, ChunkWithEmbeddingEvent> {

    // Accumulate chunks into batches of 64 before calling the API
    // (64 chunks = 1 API call instead of 64 API calls — 64x fewer requests)
    private final List<ChunkEvent> batch = new ArrayList<>();

    @Override
    public void asyncInvoke(ChunkEvent chunk, ResultFuture<ChunkWithEmbeddingEvent> future) {
        batch.add(chunk);
        if (batch.size() >= 64) {
            List<String> texts = batch.stream().map(c -> c.text).toList();
            // one API call for 64 chunks
            List<float[]> embeddings = openai.embeddings(texts, "text-embedding-3-small");
            for (int i = 0; i < batch.size(); i++) {
                future.complete(new ChunkWithEmbeddingEvent(batch.get(i), embeddings.get(i)));
            }
            batch.clear();
        }
    }
}
```

**Why this matters:** The Python worker calls the embedding API once per chunk. 1,000 chunks = 1,000 API calls. The Flink operator batches 64 chunks per call. 1,000 chunks = 16 API calls. Lower cost, faster, and the API's rate limits are 64x less likely to be hit.

---

### Step 4 — Sink: Flink writes to the vector store

```java
// Flink MongoDB sink (or Qdrant, pgvector — connectors exist for all)
embedded.sinkTo(
    MongoDBSink.<ChunkWithEmbeddingEvent>builder()
        .setUri("mongodb+srv://...")
        .setDatabase("cuga_knowledge")
        .setCollection("ai_feed_chunks")
        .setDocumentSerializer(chunk -> new Document()
            .append("_id",        chunk.id)
            .append("text",       chunk.text)
            .append("embedding",  chunk.embedding)   // float[] stored as BSON array
            .append("post_id",    chunk.postId)
            .append("source",     chunk.source)
            .append("title",      chunk.title)
            .append("url",        chunk.url)
            .append("posted_at",  chunk.postedAt)
        )
        .build()
);
```

---

## The Full Pipeline as a Diagram

```
enterprise.docs.ai_feed (Kafka)
          │
          │  Flink reads — 1 record per post
          ▼
  ┌───────────────┐
  │  KafkaSource  │  PostContentEvent { post_id, source, title, text, url, posted_at }
  └───────┬───────┘
          │  1 post
          ▼
  ┌────────────────────┐
  │  FlatMap (chunking)│  splits text into 500-token segments
  └───────┬────────────┘
          │  N chunks per post (e.g., 8 chunks for a long post)
          ▼
  ┌────────────────────────────────┐
  │  AsyncIO (AI Model Inference)  │  batches 64 chunks per API call
  │  model: text-embedding-3-small │  each chunk → 1536-dim float vector
  └───────┬────────────────────────┘
          │  N ChunkWithEmbeddingEvents
          ▼
  ┌────────────────┐
  │  MongoDB Sink  │  upsert by chunk id (idempotent)
  └───────┬────────┘
          │
          ▼
  Vector Store
  (CUGA's knowledge_search queries this)
```

---

## How Flink Handles Failures (Checkpointing)

This is where Flink's design diverges from the Python worker.

The Python worker commits a Kafka offset only after the upsert succeeds. Simple and correct, but single-threaded — it processes one chunk at a time.

Flink uses **checkpointing**: every N seconds, Flink pauses the pipeline, saves its full state (current Kafka offsets + any in-flight batch state) to durable storage (S3, HDFS, local disk), and resumes. If Flink crashes:

```
Flink checkpoint (every 30s):
  "I have consumed enterprise.docs.ai_feed up to offset 142"
  "I have 12 chunks in-flight waiting for embedding API response"
  "MongoDB has chunks through post_id abc123"

Flink crashes at offset 147

Flink restarts:
  Restores checkpoint state
  Rewinds Kafka consumer to offset 142
  Re-processes offsets 142-147
  The MongoDB upsert is idempotent (same chunk_id = overwrite)
  No duplicate embeddings. No lost chunks.
```

The Python worker achieves the same guarantee through commit-after-success. Flink achieves it through checkpointing + idempotent sinks. At scale, Flink's approach is more efficient because it doesn't block the entire pipeline waiting for each individual upsert to confirm.

---

## Kafka's Role in the Flink Pipeline

To make it concrete — here is everything Kafka does and does not do in Phase 3c-1:

**Kafka's jobs:**
1. Receive `PostContentEvent` from the feed watcher (`producer.produce()`)
2. Hold the events durably (7-day retention) in `enterprise.docs.ai_feed`
3. Deliver events to Flink's KafkaSource at the rate Flink can process them (backpressure via consumer lag)
4. Track Flink's consumer group offset (`flink-knowledge-pipeline`)
5. Receive chunks with embeddings if Flink writes intermediate results to a Kafka topic (optional in this pipeline — not needed here since we go directly to the vector store)

**Kafka's non-jobs:**
- Kafka does not chunk the text
- Kafka does not call the embedding model
- Kafka does not write to MongoDB
- Kafka has no awareness of embeddings, vectors, or AI

Flink does all the transformation. Kafka handles all the transport and durability.

---

## Why Not Just Use the Python Worker?

For 14 feeds at 15-minute intervals, the Python worker is fine. Here is where you'd switch to Flink:

| Signal | Python Worker | Switch to Flink |
|---|---|---|
| Posts per day | < 500 | > 5,000 |
| Embedding latency matters | No | Yes (Flink batches → lower p99) |
| Number of sources | 14 feeds | 100+ sources (Kafka connectors for each) |
| Embedding pipeline needs joins | No | Yes (e.g., enrich chunks with CRM data) |
| Need exactly-once guarantee | Nice to have | Required (compliance) |
| Ops team available | No | Yes |

For the feed watcher as a standalone tool: Python worker. For the feed watcher as part of an enterprise data platform with many source systems: Flink.

---

## Running Phase 3c-1

```bash
# Assumes Phase 3a is already running
# (Kafka up, cuga.tasks.ai_feed topic exists, workers running)

# 1. Create the new topic for post content
kafka-topics.sh --create \
  --topic enterprise.docs.ai_feed \
  --partitions 3 \
  --replication-factor 1 \
  --bootstrap-server localhost:9092

# 2. Start Flink (local mode for dev)
./bin/start-cluster.sh

# 3. Submit the knowledge pipeline job
./bin/flink run \
  -c cuga_runtime.flink.KnowledgePipelineJob \
  cuga-runtime-flink.jar \
  --kafka-bootstrap localhost:9092 \
  --mongodb-uri mongodb://localhost:27017 \
  --embedding-model text-embedding-3-small

# 4. Feed watcher now publishes to BOTH topics
#    (enterprise.docs.ai_feed is new — update service.py to produce there too)
#    Assessment flow (3a) is unchanged.
```

The Flink job runs continuously. As the feed watcher produces new post content events, Flink picks them up, chunks and embeds them, and upserts into the vector store — usually within a few seconds of the post being published.
