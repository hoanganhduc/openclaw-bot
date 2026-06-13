---
name: modal-research-compute
description: Use when a research or engineering task needs automatic heavy-compute routing through the local broker for Modal-backed remote CPU, high-memory CPU, or GPU execution.
user-invocable: true
disable-model-invocation: true
metadata: {"openclaw":{"requires":{"bins":["python3","modal"]}}}
---

# Modal Research Compute

Use this skill when the task is about:

- exhaustive search
- object enumeration
- counterexample hunting
- large parameter sweeps
- remote execution of generated experiment code
- GPU-suitable document, embedding, reranking, or tensor workloads

This skill is the OpenClaw integration layer for the local `research_compute` broker.

## When to prefer this skill

- the user wants Modal involved automatically
- the local machine is CPU, memory, disk, or GPU constrained for the requested workload
- the workload is long-running enough that remote execution is a better fit

## Core workflow

1. If local resources matter, run `get-available-resources`.
2. Build a broker manifest JSON for the task.
3. Run broker `plan`.
4. If the plan stays within policy, run broker `submit`.
5. Use `wait` and `fetch` to retrieve results and logs back to local storage.

## Runtime commands

Doctor:

```bash
exec: /workspace/skills/modal-research-compute/run_modal_research_compute.sh doctor
```

Plan:

```bash
exec: /workspace/skills/modal-research-compute/run_modal_research_compute.sh plan /path/to/job.json
```

Submit:

```bash
exec: /workspace/skills/modal-research-compute/run_modal_research_compute.sh submit /path/to/job.json
```

Wait:

```bash
exec: /workspace/skills/modal-research-compute/run_modal_research_compute.sh wait <job_id>
```

Fetch:

```bash
exec: /workspace/skills/modal-research-compute/run_modal_research_compute.sh fetch <job_id> --dest /path/to/output
```

Deploy:

```bash
exec: /workspace/skills/modal-research-compute/run_modal_research_compute.sh deploy
```

## Operational notes

- The broker is the decision boundary. Do not call Modal directly from the normal OpenClaw flow when the broker can handle the task.
- CPU-heavy combinatorial workloads should default to remote CPU or high-memory CPU, not GPU.
- GPU use should be explicit in the manifest or clearly justified by the workload.
- `doctor` and `plan` work without a deployed Modal app. `submit`, `wait`, `fetch`, and `deploy` need the host to be Modal-ready.
- Linux hosts become Modal-ready after `python3 -m pip install --user --upgrade modal` and `modal token set` or `modal token new`.
- Broker state persists under the OpenClaw research data tree, while fetched outputs materialize under the caller workspace by default.
