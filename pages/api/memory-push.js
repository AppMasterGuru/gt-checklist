/**
 * memory-push.js
 * POST /api/memory-push
 *
 * Receives raw MEMORY.md content from the local sync script and stores it in
 * Redis so that /api/memory can serve it on Vercel (where the local filesystem
 * is unavailable).
 *
 * Called by scripts/sync_checklist.py at end of every work session.
 *
 * Request body (JSON):
 *   {
 *     "memory_content":  string  — raw MEMORY.md text
 *     "last_modified":   string  — ISO timestamp of file mtime (optional)
 *   }
 *
 * Security: same AGENT_SECRET Bearer token as agent-push.
 *
 * Redis key written: gt:memory_content
 *   { content: string, last_modified: string, synced_at: string }
 */

import { redisSet } from '../../lib/redis';

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  // Auth — same pattern as agent-push.js
  const agentSecret = process.env.AGENT_SECRET;
  if (agentSecret) {
    const authHeader  = req.headers['authorization'] || '';
    const bodySecret  = req.body?.secret || '';
    const headerToken = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : authHeader;
    if (headerToken !== agentSecret && bodySecret !== agentSecret) {
      return res.status(403).json({ error: 'Invalid secret' });
    }
  }

  const { memory_content, last_modified } = req.body || {};

  if (!memory_content || typeof memory_content !== 'string') {
    return res.status(400).json({ error: 'memory_content (string) is required' });
  }

  const synced_at = new Date().toISOString();

  await redisSet('gt:memory_content', {
    content:       memory_content,
    last_modified: last_modified || synced_at,
    synced_at,
  });

  return res.status(200).json({
    ok:      true,
    length:  memory_content.length,
    synced_at,
  });
}
