/**
 * tasks.js
 * GET  /api/tasks  — returns current task state from Redis
 * POST /api/tasks  — toggles a single task { id, done }
 *
 * State is stored in Redis (gt:state key) and populated by sync_checklist.py.
 * MEMORY.md is the authoritative source — this endpoint is a simple Redis
 * read/write layer for task state persistence.
 */

import { redisGet, redisSet } from '../../lib/redis';

export default async function handler(req, res) {
  if (req.method === 'GET') {
    const state = (await redisGet('gt:state')) || {};
    return res.status(200).json({ state });
  }

  if (req.method === 'POST') {
    const { id, done } = req.body;
    if (!id || typeof done !== 'boolean') {
      return res.status(400).json({ error: 'Missing id or done' });
    }
    const state = (await redisGet('gt:state')) || {};
    state[id] = done;
    await redisSet('gt:state', state);
    return res.status(200).json({ ok: true, state });
  }

  res.status(405).json({ error: 'Method not allowed' });
}
