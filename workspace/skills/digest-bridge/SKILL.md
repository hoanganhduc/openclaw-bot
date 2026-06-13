---
name: digest_bridge
description: Extract paper identifiers from research/RSS digests and create getscipapers manifests for retrieval.
user-invocable: true
disable-model-invocation: true
metadata: {"openclaw":{"requires":{"bins":["python3"]}}}
---

# Digest Bridge

Use this skill to bridge digest outputs to paper retrieval. It scans digests, extracts arXiv IDs and DOIs, deduplicates, and creates getscipapers manifests.

The user provides their request inline with the command. If the request is unclear or empty, show the usage guide below and do nothing else.

## Usage guide (show when no arguments given)

```
/digest_bridge <action> [options]

Scan (dry run):
  /digest_bridge scan
  /digest_bridge scan --source research
  /digest_bridge scan --source rss
  /digest_bridge scan --min-score 80

Create manifest & request papers:
  /digest_bridge request
  /digest_bridge request --watch
  /digest_bridge request --min-score 80
  /digest_bridge request --source rss --watch

  Options:
    --source research|rss|all   Which digest to scan (default: all)
    --min-score N               Minimum relevance score (default: 0)
    --watch                     Also create watches for monitoring
```

## Execution

```
exec: python3 /workspace/skills/digest-bridge/digest_bridge.py <COMMAND AND ARGS>
```
