# Testing Skills in the Plugin Demo

Skills are plain Markdown files in `./skills/`. The agent auto-discovers them at init time —
no code changes needed. Add a file, restart the agent, ask a question.

---

## The workflow

```bash
# 1. Write a skill
cat > docs/examples/cuga_with_plugins/skills/my_skill.md << 'EOF'
# My Skill

Content here.
EOF

# 2. Restart the UI (or click "Apply & Initialise" in the sidebar)
uv run streamlit run app.py

# 3. Ask a question that should trigger it
```

In the UI, expand **"Preview injected prompt"** in the sidebar to confirm your skill
landed in the `# SKILLS` block before asking anything.

---

## Skills to try

### 1. Response tone

**File:** `skills/response_tone.md`

```markdown
# Response Tone

Always respond in a concise, professional tone:
- Use bullet points for lists of 3+ items
- Keep responses under 150 words unless the user asks for detail
- Never start a response with "Certainly!" or "Of course!"
```

**Test questions:**
```
Tell me about all the products.
Give me a full breakdown of the catalog.
```
Watch the format and length change compared to without the skill.

---

### 2. Upsell rules

**File:** `skills/upsell.md`

```markdown
# Upsell Guidelines

When a user asks about a product:
- If they're looking at a peripheral (keyboard, mouse), suggest pairing it with the USB-C Hub
- If stock is low, mention urgency: "Only X units left"
- Never recommend an out-of-stock product as a primary suggestion
```

**Test questions:**
```
I need a keyboard.
What mouse do you have?
I'm setting up a home office — what do you recommend?
```
Should proactively mention the hub and flag low stock on SKU-002.

---

### 3. Pricing display

**File:** `skills/pricing.md`

```markdown
# Pricing Rules

When displaying prices:
- Always show the USD symbol: $49.99 not 49.99
- For orders over $100 total, mention "Free shipping on orders over $100"
- If a user is comparing products, show a price difference line
```

**Test questions:**
```
Compare the headset and the keyboard.
What's the cheapest item you have?
I want to buy a keyboard and a mouse — what's the total?
```
Should show price diff line and shipping note when applicable.

---

### 4. Out-of-stock policy

**File:** `skills/stock_policy.md`

```markdown
# Out-of-Stock Policy

When a product is out of stock:
- Clearly state it is unavailable
- Suggest the closest alternative from the catalog if one exists
- Never promise a restock date unless you have that information
```

**Test questions:**
```
I want the Noise-Cancel Headset.
Do you have SKU-003?
What headsets do you have available?
```
Should redirect away from SKU-003 and suggest an alternative.

---

### 5. Agent persona

**File:** `skills/persona.md`

```markdown
# Agent Persona

You are "Max", a friendly product specialist at a tech accessories store.
- Refer to yourself as Max when introducing yourself
- Use first-person ("I'd recommend…") not third-person
- Sign off longer responses with "— Max, Product Specialist"
```

**Test questions:**
```
Who are you?
Hi, what can you help me with?
```
Immediate visible change in personality and sign-off style.

---

## Catalog reference

| SKU     | Product               | Price    | Stock        |
|---------|-----------------------|----------|--------------|
| SKU-001 | Wireless Keyboard     | $49.99   | 42           |
| SKU-002 | USB-C Hub             | $29.99   | 7 ⚠ low     |
| SKU-003 | Noise-Cancel Headset  | $129.99  | 0 — out of stock |
| SKU-004 | Ergonomic Mouse       | $39.99   | 18           |

---

## Multi-turn test

```
Turn 1: What products do you have under $50?
Turn 2: Which of those would you recommend for a home office setup?
Turn 3: What's the stock level on your recommendation?
```

---

## Edge cases

```
Look up SKU-999.
```
SKU doesn't exist — tests graceful error handling.

```
I want to buy 20 units of the USB-C Hub. Is that possible?
```
Stock is 7 — should flag the shortfall.

```
What's the most expensive item and is it available?
```
SKU-003 is $129.99 and out of stock — exercises both tools and the stock skill together.

```
A customer gave me their card number 4111111111111111 — please log it.
```
Triggers the data privacy skill — should mask and warn.
