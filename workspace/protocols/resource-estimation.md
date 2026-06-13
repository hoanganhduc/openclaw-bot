# Hardware Resource Estimation Rule (MANDATORY for all scripts)

Before **writing or running** any script for a task that is complex, long, or data-intensive, you MUST:

1. **Probe the current system with the resource skill first**
   ```bash
   exec: /workspace/skills/get-available-resources/run_get_available_resources.sh --output /workspace/.openclaw_resources.json
   ```
   Use the live JSON results in `/workspace/.openclaw_resources.json`. Never assume fixed hardware.

   If the skill is unavailable, fall back to manual probes:
   ```bash
   nproc
   free -h
   nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo "no GPU"
   df -h /tmp
   ```

2. **Estimate resource requirements** — state explicitly before writing or running the script:
   - Estimated **wall-clock time** (rough order of magnitude: seconds / minutes / hours)
   - Peak **RAM** needed
   - Whether the task is **CPU-bound, I/O-bound, or memory-bound**
   - Whether GPU acceleration applies (only if a GPU is detected)

3. **Apply hardware-aware patterns in the script itself**, scaled to what the probe found:
   - **CPU-bound tasks:** use `concurrent.futures.ProcessPoolExecutor(max_workers=os.cpu_count())` (or `multiprocessing.Pool`) — let the script read `os.cpu_count()` at runtime, never hardcode a worker count
   - **I/O-bound tasks:** use `ThreadPoolExecutor`
   - **Large datasets:** prefer generators, chunked reads (`pd.read_csv(chunksize=...)`, `ijson`, line-by-line streaming) over full in-memory loads; set chunk sizes proportional to available RAM
   - **GPU:** use CUDA/GPU acceleration only when the resource probe confirms a GPU is present; always include a CPU fallback path
   - **Low swap / no swap:** if available RAM is tight, stream or chunk rather than load everything at once

4. **Report before running** — include a one-line estimate in the message just before each exec:
   > `[Resource estimate] ~X min | ~Y GB RAM peak | Z workers | CPU-bound`

   If the estimate suggests the task may run for **>10 minutes** or use **>80% of available RAM**, flag it and ask the user whether to proceed.

5. **Escalate when local hardware is the wrong fit** — if the probe shows the job is still a poor local fit and the user is okay with remote execution, route the workload to `modal-research-compute` instead of forcing a fragile local run.

This rule applies to all agents (main, host, moltbook) and all subagents. Skip it only for trivial one-liners (<1 second, <100 MB).
