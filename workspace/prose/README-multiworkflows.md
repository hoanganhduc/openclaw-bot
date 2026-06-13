# OpenProse + fallback skill pack

Fallback skill:
- {{ OPENCLAW_WORKSPACE }}/skills/agent_group_discuss/SKILL.md

OpenProse workflows:
- {{ OPENCLAW_WORKSPACE }}/prose/agent_group_discussion.prose
- {{ OPENCLAW_WORKSPACE }}/prose/agent_group_review.prose
- {{ OPENCLAW_WORKSPACE }}/prose/agent_group_research.prose

Run state:
- {{ OPENCLAW_WORKSPACE }}/group_discuss/runs
- {{ OPENCLAW_WORKSPACE }}/.prose/runs

Before use:
1. Edit model placeholders in the .prose files.
2. Restart Gateway if /prose is not visible yet.
3. Optionally create:
   - {{ OPENCLAW_WORKSPACE }}/skills/agent_group_discuss/MODEL_TIERS.md
   - {{ OPENCLAW_WORKSPACE }}/prose/MODEL_TIERS.md

Useful commands:
  /prose help
  /prose compile {{ OPENCLAW_WORKSPACE }}/prose/agent_group_discussion.prose
  /prose compile {{ OPENCLAW_WORKSPACE }}/prose/agent_group_review.prose
  /prose compile {{ OPENCLAW_WORKSPACE }}/prose/agent_group_research.prose

Examples:
  /agent_group_discuss
  topic: Compare local models vs hosted APIs
  mode: discussion
  rounds: 2
  interaction: panel_judge
  output: concise recommendation

  /prose run {{ OPENCLAW_WORKSPACE }}/prose/agent_group_review.prose
