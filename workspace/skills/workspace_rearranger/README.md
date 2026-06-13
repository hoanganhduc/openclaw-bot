workspace_rearranger
====================

This skill installs a conservative workspace organizer intended for OpenClaw with sandboxing enabled and workspaceAccess set to rw. Inside the sandbox, the real workspace is mounted at /workspace.

Typical usage in chat:
- “rearrange the workspace for me”
- “organize new files in inbox only”
- `/skill workspace_rearranger dry-run`

Important notes:
- First whole-workspace pass should usually be dry-run + report-only.
- Scheduled runs are installed as isolated cron jobs with --no-deliver by default.
- The backend writes reports/manifests under _logs/organizer and can undo the most recent completed run.
