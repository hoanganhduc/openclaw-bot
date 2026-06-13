# MODEL_TIERS.example.md

Copy this to MODEL_TIERS.md and replace placeholders with your actual models.

- name: YOUR_STRONG_REASONER
  reasoning: very_high
  context: large
  speed: medium
  cost: high
  best_for:
    - planner
    - correctness review
    - judge
    - critic
    - deep synthesis

- name: YOUR_BALANCED_MODEL
  reasoning: high
  context: large
  speed: medium
  cost: medium
  best_for:
    - review
    - specialist roles

- name: YOUR_FAST_OR_BALANCED_MODEL
  reasoning: medium
  context: medium
  speed: fast
  cost: low
  best_for:
    - scouting
    - brainstorming
    - lightweight review
    - pragmatic analysis
