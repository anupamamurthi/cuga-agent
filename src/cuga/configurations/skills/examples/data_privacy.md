# Data Privacy

Applies whenever the agent handles personally identifiable information (PII) such as names, email addresses, phone numbers, or financial data.

## When to use
- Any task that reads, writes, or displays user records
- Before returning data that may contain PII in the final answer
- When exporting data to files or external systems

## Guidelines
- Never log or print raw PII to intermediate code execution outputs unless necessary for debugging a specific step
- When presenting PII in the final answer, mask sensitive fields unless the user explicitly requested the full value (e.g. show `j***@example.com` instead of `john@example.com`)
- Do not store PII in variables beyond the scope of the current task
- If a task would cause PII to be written to an unencrypted file or external destination, warn the user before proceeding
