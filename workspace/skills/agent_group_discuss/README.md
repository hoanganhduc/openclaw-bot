# agent_group_discuss

This is the conversational fallback skill.

Support files:
- `TEMPLATES.md`
- `EXECUTION.md`
- `MODEL_TIERS.md`

It can be used by:
- normal language requests
- /agent_group_discuss

Named templates:
- Lakatos Proof & Refutation
- Pólya Multi-Strategy
- Knuth Manuscript Review
- Structured Research Team
- Graph Reconfiguration Specialist
- Lean Formalization Team

Examples:

/agent_group_discuss
topic: Should we use retrieval or long-context for internal docs?
mode: research
rounds: 3
max_agents: 4
interaction: panel_judge
output: decision memo
constraints:
- keep it practical
- compare reliability, cost, and complexity
