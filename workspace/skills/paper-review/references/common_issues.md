# Common Issues Checklist

Use this as a lightweight internal checklist during review-only passes.

## Logic and math

- unstated assumptions in a proof step
- a lemma used outside its stated domain
- missing case splits or boundary cases
- circular reasoning or restating the claim as evidence
- undefined notation or symbols that change meaning later

## Evidence and methodology

- unsupported causal claims
- sample size or power not justified
- missing controls or ablations
- statistical significance reported without effect size or uncertainty
- train/test leakage or evaluation on tuning data

## Presentation

- theorem or claim text stronger than what is proved
- figure/table captions that do not match the text
- related-work claim that omits the main baseline
- ambiguous experimental setup
- references or datasets not actually cited where needed
