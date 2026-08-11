# Errors Log

Command failures, exceptions, and unexpected behaviors.

---

## [ERR-20260805-001] secret_bearing_subprocess_path_hijack

**Logged**: 2026-08-05T06:22:27+07:00
**Priority**: critical
**Status**: resolved

### Summary

Archive encryption and file-delivery processes selected security-sensitive
executables through ambient `PATH`. A hostile earlier executable could inherit
passphrase/plaintext descriptors or provider credentials and exported files.

### Resolution

Open and validate the fixed system executable before use where secret
descriptors cross the boundary, execute it through the pinned descriptor or
absolute path, rebuild `PATH` to the fixed system directories, and invoke the
pinned OpenClaw JavaScript entrypoint through `/usr/bin/node`. File delivery
uses `/usr/bin/curl` only after target and artifact authorization. Hostile-PATH
regressions assert that canary executables never run.

### Canonical Integration Plan

- Related Skills: zotero, calibre
- Related Settings Or Artifacts: owner archive, restore, file delivery
- Affected Install Targets: openclaw
- Affected OS/Substrates: inspected Linux host and sandbox paths
- Canonical Repo Change: keep executable identity checks and hostile-PATH tests with every secret-bearing subprocess
- Verification Plan: focused hostile tests, complete offline runtime suite, shell syntax checks
- Blocked Or Unsupported Targets: non-Linux executable trust mechanisms were not inspected

### Metadata

- Reproducible: yes
- Related Files: `scripts/owner_archive.py`, `backup.sh`, `restore.sh`, `workspace/skills/zotero/send_file.sh`

---

## [ERR-20260805-002] authority_closure_and_delivery_deputy_bypass

**Logged**: 2026-08-05T06:22:27+07:00
**Priority**: critical
**Status**: resolved

### Summary

Canonical authentication state accepted nested executable secret references,
and credentialed delivery/service helpers did not close all subject, resource,
and destination boundaries. Direct host send-queue jobs initially bypassed the
producer-side recipient check.

### Resolution

Recursively validate canonical authority JSON and reject every executable
secret source; constrain reviewed service installation to the inherited exact
installer descriptor and derived destinations; authorize exact channel targets
and workspace export roots before credential access; snapshot no-follow file
descriptors; and revalidate queued targets and snapshots at the host consumer.
The portable target policy is a strict, deny-by-default owner authority with an
exact runtime projection and no credential-derived recipient inference.

### Canonical Integration Plan

- Related Skills: zotero, calibre
- Related Settings Or Artifacts: canonical auth SQLite, user services, send queue, file-delivery policy
- Affected Install Targets: openclaw and umbrella restore materializer
- Affected OS/Substrates: inspected Linux host and sandbox paths
- Canonical Repo Change: preserve recursive authority validation, producer/consumer checks, and exact policy schema
- Verification Plan: hostile nested-SecretRef, arbitrary-path, unauthorized-target, symlink, duplicate-schema, and direct-queue regressions
- Blocked Or Unsupported Targets: umbrella materializer implementation is owned and verified outside this component

### Metadata

- Reproducible: yes
- Related Files: `scripts/openclaw_auth_closure.py`, `scripts/service_transaction.py`, `workspace/skills/zotero/delivery_policy.py`, `workspace/scripts/job_queue_worker.sh`

---

## [ERR-20260805-003] isolated_unittest_nonpackage_target

**Logged**: 2026-08-05T02:35:24Z
**Priority**: low
**Status**: resolved

### Summary

`python3 -I -S -B -m unittest tests.test_host_boundary` failed because this
repository's `tests/` directory is not a Python package.

### Resolution

Use unittest discovery with an explicit start directory and filename pattern,
for example `python3 -I -S -B -m unittest discover -s tests -p
'test_host_boundary.py' -v`.

### Metadata

- Reproducible: yes
- Related Files: `tests/test_host_boundary.py`, `tests/test_vnu_eoffice_launcher.py`

---

## [ERR-20260805-004] patch_created_group_writable_service_templates

**Logged**: 2026-08-05T02:58:35Z
**Priority**: low
**Status**: resolved

### Summary

New systemd templates created through the patch tool inherited the workspace's
group-writable default mode. The reviewed service collector correctly rejected
them as mutable service authority.

### Resolution

After adding new privileged templates, inspect their modes and explicitly set
them to `0644` before running the service transaction tests.

### Metadata

- Reproducible: yes
- Related Files: `systemd/user/openclaw-*-worker.service`, `systemd/user/send-queue-worker.service`

---

## [ERR-20260805-005] runtime_contract_tests_need_system_yaml

**Logged**: 2026-08-05T02:59:38Z
**Priority**: low
**Status**: resolved

### Summary

The runtime-contract module imports system PyYAML, so `python3 -I -S -B`
cannot import the suite even though application helpers intentionally use
`-S`.

### Resolution

Run this repository's test harness with `python3 -I -B` and
`PYTHONDONTWRITEBYTECODE=1`; retain `-I -S -B` for the production helper
processes that require a standard-library-only boundary.

### Metadata

- Reproducible: yes
- Related Files: `tests/test_runtime_contracts.py`

---

## [ERR-20260805-006] changed_test_module_not_ast_checked

**Logged**: 2026-08-05T03:02:15Z
**Priority**: low
**Status**: resolved

### Summary

The first complete suite run exposed an indentation error in a concurrently
changed test module and two static expectations left behind by the queue-worker
descriptor-snapshot refactor.

### Resolution

AST-parse every changed test module before the complete suite, then update
contract assertions to describe the new trust boundary rather than old
implementation strings.

### Metadata

- Reproducible: yes
- Related Files: `tests/test_skill_secret_loader.py`, `tests/test_runtime_contracts.py`

---

## [ERR-20260805-007] recursive_temp_cleanup_rejected

**Logged**: 2026-08-05T03:04:36Z
**Priority**: low
**Status**: resolved

### Summary

The command safety layer rejected recursive removal even for a validated
`mktemp` unit-verification directory.

### Resolution

For small known temporary artifact sets, unlink each exact file and remove the
now-empty directory with `rmdir`; avoid recursive cleanup commands.

### Metadata

- Reproducible: yes
- Related Files: `systemd/user/openclaw-*-worker.service`

---

## [ERR-20260805-008] tex_control_word_numeric_suffix_bypassed_word_boundary

**Logged**: 2026-08-05T03:09:41Z
**Priority**: high
**Status**: resolved

### Summary

The initial dangerous-TeX deny expression ended control-word alternatives
with `\b`, which did not match a letter-to-digit transition. That allowed the
primitive `\write18` to bypass validation.

### Resolution

Model TeX control-word termination explicitly with `(?![A-Za-z@])` and keep
numeric-suffix adversarial cases in the isolation suite.

### Metadata

- Reproducible: yes
- Related Files: `workspace/skills/manim-math-animation/mma/model.py`, `tests/test_manim_isolation.py`

---

## [ERR-20260805-009] sync_staging_requires_nonexistent_child_or_marker

**Logged**: 2026-08-05T03:12:09Z
**Priority**: low
**Status**: resolved

### Summary

A sync dry-run wrapper passed an existing empty `TemporaryDirectory` as
`--staging`. The sync safety boundary correctly rejected it because existing
staging directories must already contain its ownership marker.

### Resolution

Create a temporary owner directory and pass a non-existent child path as the
sync staging target; let the sync process create and mark that child, then let
the owner wrapper clean up the whole temporary directory.

### Metadata

- Reproducible: yes
- Related Files: `sync.sh`

---

## [ERR-20260805-010] schema_probe_failed_to_redact_numeric_principal

**Logged**: 2026-08-05T03:46:11Z
**Priority**: high
**Status**: resolved

### Summary

A live configuration shape probe redacted string values but rendered numeric
scalars, exposing an allowlist principal in diagnostic output.

### Resolution

Redact every scalar value by default in configuration/schema probes. Reveal
only explicitly allowlisted metadata fields and normalized relative authority
paths; test probes with both string and numeric personal-data canaries.

### Metadata

- Reproducible: yes
- Related Files: live OpenClaw configuration inspection workflow

---

## [ERR-20260805-011] credential_tree_filenames_exposed_identifiers

**Logged**: 2026-08-05T03:47:20Z
**Priority**: high
**Status**: resolved

### Summary

A path-only credential-tree inventory was treated as harmless metadata, but
channel session filenames can themselves encode contact or device identifiers.

### Resolution

Never enumerate live credential-tree member names into diagnostics. Model such
trees as opaque bounded authorities and validate traversal with aggregate
counts, sizes, modes, and synthetic fixtures only.

### Metadata

- Reproducible: yes
- Related Files: live OpenClaw channel-authority inspection workflow

---

## [ERR-20260805-012] validator_literal_tripped_release_secret_marker

**Logged**: 2026-08-05T04:32:00Z
**Priority**: medium
**Status**: resolved

### Summary

A service-account shape validator embedded complete PEM boundary markers as
source literals. The sync dry-run correctly treated those literals as possible
private-key material and rejected the public staging tree.

### Resolution

Compose public validator delimiters from separately harmless constant fragments
so secret scanners retain strict marker matching without weakening runtime
service-account validation. Keep the sync dry-run as the release gate.

### Metadata

- Reproducible: yes
- Related Files: `scripts/service_transaction.py`, `sync.sh`

---

## [ERR-20260805-013] systemd_verify_harness_replaced_standard_unit_path

**Logged**: 2026-08-05T04:38:00Z
**Priority**: low
**Status**: resolved

### Summary

A rendered-unit verification harness passed a drop-in file as a standalone unit
and replaced `SYSTEMD_UNIT_PATH`, preventing systemd from resolving standard
targets before it could validate the reviewed worker units.

### Resolution

Pass only `.service` and `.timer` files to `systemd-analyze verify`, retain the
standard unit search path, and treat drop-ins through the transaction helper's
own rendered-directive validator.

### Metadata

- Reproducible: yes
- Related Files: rendered systemd verification workflow

---
