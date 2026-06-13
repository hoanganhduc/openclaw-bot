# Model Tiers Template

Copy this file to `MODEL_TIERS.md` and edit it with your actual models.

- name: YOUR_STRONG_REASONER
  reasoning: very_high
  context: very_large
  speed: medium
  cost: high
  best_for:
    - judge
    - synthesis
    - formal critique
  avoid_for:
    - cheap scouting

- name: YOUR_BALANCED_MODEL
  reasoning: high
  context: large
  speed: medium
  cost: medium
  best_for:
    - planner
    - review
    - research synthesis

- name: YOUR_FAST_OR_BALANCED_MODEL
  reasoning: medium
  context: medium
  speed: fast
  cost: low
  best_for:
    - brainstorming
    - scouting
    - lightweight summarization
