# Currency Conversion

Applies whenever the user asks about amounts in a different currency than what the tools return.

## When to use
- User asks to "show amounts in EUR" but the API returns USD
- Any task that involves monetary values where a currency mismatch may exist
- User explicitly mentions a target currency

## Guidelines
- Always state which exchange rate source was used (or that live rates were unavailable)
- Round converted amounts to 2 decimal places
- Show both the original value and the converted value in the final answer
- If live exchange rate data is not available via a tool, note that the conversion is approximate and use a widely accepted reference rate
