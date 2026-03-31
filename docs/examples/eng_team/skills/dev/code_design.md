# Code Design

When designing an implementation:

- Apply SOLID principles; highlight which principle is most relevant per component
- Prefer composition over inheritance
- Keep functions under 30 lines; classes under 300 lines
- Identify the single responsibility of each module before writing a line
- Flag any design decisions that trade correctness for performance — document the trade-off
- If touching shared infrastructure, note blast radius (who else is affected)
- Propose the simplest design that passes all acceptance criteria — avoid speculative abstractions
