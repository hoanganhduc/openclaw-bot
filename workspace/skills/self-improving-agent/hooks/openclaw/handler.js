/**
 * Self-Improvement Hook for OpenClaw
 *
 * Fires on:
 *   - agent:bootstrap — injects a dynamic reminder (pending count + titles)
 *   - command:reset   — prompts to capture learnings before starting fresh
 */

const { readFileSync, existsSync } = require('node:fs');
const { join } = require('node:path');

const LEARNINGS_DIR = join(process.env.OPENCLAW_WORKSPACE ?? join(process.env.HOME ?? '', '.openclaw', 'workspace'), '.learnings');

function parsePendingSummary() {
  const files = ['ERRORS.md', 'LEARNINGS.md', 'FEATURE_REQUESTS.md'];
  const high = [];
  const recent = [];
  let total = 0;

  for (const file of files) {
    const filePath = join(LEARNINGS_DIR, file);
    if (!existsSync(filePath)) continue;
    let content;
    try {
      content = readFileSync(filePath, 'utf-8');
    } catch {
      continue;
    }

    // Match pending entries: ## [ID] title ... **Status**: pending
    const entryRegex = /^## (\[[A-Z]+-\d{8}-[A-Z0-9]+\] .+?)$([\s\S]*?)(?=^## |(?![\s\S]))/gm;
    let match;
    while ((match = entryRegex.exec(content)) !== null) {
      const header = match[1].trim();
      const body = match[2];
      if (!body.includes('**Status**: pending')) continue;
      total++;
      recent.push(header);
      if (body.includes('**Priority**: high') || body.includes('**Priority**: critical')) {
        high.push(header);
      }
    }
  }

  return { total, high, recent: recent.slice(-3) };
}

function buildBootstrapReminder(summary) {
  const lines = ['## Self-Improvement'];

  if (summary.total === 0) {
    lines.push('No pending learnings. Log to `.learnings/` when:');
    lines.push('- User corrects you → LEARNINGS.md (category: correction)');
    lines.push('- Command fails unexpectedly → ERRORS.md');
    lines.push('- User requests missing capability → FEATURE_REQUESTS.md');
    lines.push('- Better approach discovered → LEARNINGS.md (category: best_practice)');
  } else {
    lines.push(`**${summary.total} pending item${summary.total > 1 ? 's' : ''}** in \`.learnings/\``);
    if (summary.high.length > 0) {
      lines.push('');
      lines.push(`⚠️ High-priority: ${summary.high.map(h => `\`${h}\``).join(', ')}`);
    }
    if (summary.recent.length > 0) {
      lines.push('');
      lines.push('Recent: ' + summary.recent.map(r => `\`${r}\``).join(', '));
    }
    lines.push('');
    lines.push('Review and promote applicable learnings to SOUL.md / AGENTS.md / TOOLS.md / DECISIONS.md.');
  }

  lines.push('');
  lines.push('**Log system/config changes to DECISIONS.md** (per workspace policy).');

  return lines.join('\n');
}

const handler = async (event) => {
  if (!event || typeof event !== 'object') return;

  const sessionKey = event.sessionKey ?? '';
  // Skip sub-agent sessions
  if (sessionKey.includes(':subagent:')) return;

  // ── Bootstrap: inject dynamic pending summary ──
  if (event.type === 'agent' && event.action === 'bootstrap') {
    if (!event.context || typeof event.context !== 'object') return;
    if (!Array.isArray(event.context.bootstrapFiles)) return;

    let summary = { total: 0, high: [], recent: [] };
    try {
      summary = parsePendingSummary();
    } catch {
      // Non-fatal: fall through with empty summary
    }

    event.context.bootstrapFiles.push({
      path: 'SELF_IMPROVEMENT_REMINDER.md',
      content: buildBootstrapReminder(summary),
      virtual: true,
    });
    return;
  }

  // ── command:reset — prompt to capture before wiping session ──
  if (event.type === 'command' && event.action === 'reset') {
    let summary = { total: 0, high: [], recent: [] };
    try {
      summary = parsePendingSummary();
    } catch {
      // ignore
    }

    // Only inject if there are pending items worth reviewing
    if (summary.total > 0 && event.context && Array.isArray(event.context.bootstrapFiles)) {
      const lines = [
        '## Before You Reset — Capture Learnings',
        '',
        `You have **${summary.total} pending item${summary.total > 1 ? 's' : ''}** in \`.learnings/\`.`,
        'Did anything from this session qualify?',
        '',
        '- User correction → LEARNINGS.md',
        '- Unexpected error → ERRORS.md',
        '- Config/system change → DECISIONS.md',
        '',
        'Log now before context is lost.',
      ];
      event.context.bootstrapFiles.push({
        path: 'SELF_IMPROVEMENT_RESET_REMINDER.md',
        content: lines.join('\n'),
        virtual: true,
      });
    }
  }
};

module.exports = handler;
module.exports.default = handler;
