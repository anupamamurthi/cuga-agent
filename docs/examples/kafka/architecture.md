# cuga watch — Architecture

Two modes, one config schema. The **classic mode** runs everything in a single
process. The **Kafka mode** splits the pipeline into a producer process and a
consumer process connected by a Kafka topic.

---

## Classic Mode (single process)

```mermaid
flowchart TD
    CFG([watch_config.json]):::config

    subgraph proc["  Single Process  "]
        direction TB
        WE["WatchExecutor"]
        Q[["asyncio Queue\n(in-memory)"]]

        subgraph shared["executor.py  (shared logic)"]
            FS["_fetch_source"]
            FM["_filter_matches"]
            DA["_dispatch_action"]
        end

        WE --> FS
        FS --> FM
        FM --> Q
        Q --> DA
    end

    subgraph sources["Sources"]
        FB["Facebook Groups"]
        WP["Web / RSS"]
    end

    subgraph actions["Actions"]
        EM["📧 Email"]
        SM["💬 SMS"]
        LG["📋 Log"]
        AG["🤖 Agent Notify"]
    end

    CFG --> WE
    FS --> FB & WP
    DA --> EM & SM & LG & AG

    classDef config fill:#f5f0ff,stroke:#7c3aed,color:#1e1b4b
    classDef shared fill:#fef9c3,stroke:#ca8a04,color:#1c1917
```

---

## Kafka Mode (separate processes)

```mermaid
flowchart LR
    CFG([watch_config_kafka.json]):::config

    subgraph producer["  Producer Process  "]
        direction TB
        KP["KafkaWatchProducer"]

        subgraph sp["executor.py"]
            FS2["_fetch_source"]
            FM2["_filter_matches"]
        end

        KP --> FS2 --> FM2
    end

    subgraph kafka["  Kafka  (Docker)  "]
        T[("cuga.watch.events\ntopic")]
    end

    subgraph consumer["  Consumer Process  "]
        direction TB
        KC["KafkaWatchConsumer"]

        subgraph sc["executor.py"]
            DA2["_dispatch_action"]
        end

        KC --> DA2
    end

    subgraph sources["Sources"]
        FB2["Facebook Groups"]
        WP2["Web / RSS"]
    end

    subgraph actions["Actions"]
        EM2["📧 Email"]
        SM2["💬 SMS"]
        LG2["📋 Log"]
        AG2["🤖 Agent Notify"]
    end

    CFG --> KP
    CFG --> KC

    FS2 --> FB2 & WP2
    FM2 -- "WatchEvent\n(JSON)" --> T
    T -- "WatchEvent\n(JSON)" --> KC
    DA2 --> EM2 & SM2 & LG2 & AG2

    classDef config fill:#f5f0ff,stroke:#7c3aed,color:#1e1b4b
```

---

## What's shared between both modes

| Component | File | Used by |
|---|---|---|
| `_fetch_source` | `executor.py` | Classic WatchExecutor + Kafka Producer |
| `_filter_matches` | `executor.py` | Classic WatchExecutor + Kafka Producer |
| `_dispatch_action` | `executor.py` | Classic WatchExecutor + Kafka Consumer |
| `WatchConfig` models | `models.py` | All three |
| `KafkaConfig` / `KafkaWatchConfig` | `kafka_models.py` | Kafka only (extends WatchConfig) |

---

## Running locally

```bash
# 1. Start Kafka broker + UI (http://localhost:8080)
docker compose -f docker/kafka/docker-compose.yml up -d

# 2. Producer — polls sources, publishes matches to topic
python -m cuga.watch.kafka_producer docs/examples/kafka/watch_config_kafka.json

# 3. Consumer — reads topic, fires actions  (separate terminal)
python -m cuga.watch.kafka_consumer docs/examples/kafka/watch_config_kafka.json
```

The producer and consumer both read the **same config file**. The `kafka` block
is the only addition over the standard `watch_config.json` schema.
