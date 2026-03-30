# Date Formatting

Applies whenever the agent reads or writes date/time values across tools that may use different formats.

## When to use
- API responses that return epoch timestamps, ISO 8601 strings, or locale-specific formats
- User asks for results "this week", "last month", "Q1 2025", or similar relative date ranges
- Tasks that compare or filter records by date

## Guidelines
- Always convert epoch milliseconds to a human-readable format before including in the final answer
- When the user specifies a relative range ("last 7 days"), compute the absolute start/end dates from the current datetime and state them explicitly
- Default output format: `YYYY-MM-DD HH:MM UTC` unless the user specifies otherwise
- When comparing dates from different APIs, normalise to UTC first
