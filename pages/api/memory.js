import fs from 'fs';
import path from 'path';
import { parseMemory } from '../../lib/parseMemory';
import { redisGet } from '../../lib/redis';

// Local filesystem fallback (works in local dev; unavailable on Vercel)
const MEMORY_PATH = path.join(
  process.env.HOME || '/Users/barnwellelliott',
  'Documents',
  'CLAUDE CODE',
  'GLOBAL TRANSPORT',
  'MEMORY.md'
);

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  // 1. Try Redis first — populated by sync_checklist.py via /api/memory-push.
  //    This is the only source that works on Vercel (no local filesystem there).
  try {
    const cached = await redisGet('gt:memory_content');
    if (cached?.content) {
      const sections = parseMemory(cached.content);
      return res.status(200).json({
        sections,
        lastModified: cached.last_modified || cached.synced_at,
        synced_at:    cached.synced_at,
        source:       'redis',
      });
    }
  } catch (_) {
    // Redis unavailable or parse error — fall through to disk
  }

  // 2. Fall back to local disk (local dev without a prior sync).
  try {
    if (!fs.existsSync(MEMORY_PATH)) {
      return res.status(404).json({
        error:  'MEMORY.md not in Redis and not found on disk.',
        hint:   'Run: python3 scripts/sync_checklist.py  to push MEMORY.md to Redis.',
        source: 'none',
      });
    }
    const raw          = fs.readFileSync(MEMORY_PATH, 'utf8');
    const sections     = parseMemory(raw);
    const lastModified = fs.statSync(MEMORY_PATH).mtime.toISOString();
    return res.status(200).json({ sections, lastModified, path: MEMORY_PATH, source: 'disk' });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
