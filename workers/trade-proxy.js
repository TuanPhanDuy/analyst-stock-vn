/**
 * Cloudflare Worker — VN Trade Proxy
 * Forwards workflow_dispatch to GitHub using GH_DISPATCH_TOKEN secret.
 */

const REPO     = 'TuanPhanDuy/analyst-stock-vn';
const WORKFLOW = 'record_trade.yml';
const CORS     = { 'Access-Control-Allow-Origin': '*' };

export default {
  async fetch(request, env) {
    // CORS pre-flight
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          ...CORS,
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type',
        },
      });
    }

    // GET /debug — verify token is configured without exposing it
    if (request.method === 'GET') {
      const token = env.GH_DISPATCH_TOKEN || '';
      return new Response(JSON.stringify({
        token_set:    token.length > 0,
        token_length: token.length,
        token_prefix: token.slice(0, 4),   // shows "ghp_" or "gith" — safe to expose
        token_valid_format: token.startsWith('ghp_') || token.startsWith('github_pat_'),
      }), {
        headers: { ...CORS, 'Content-Type': 'application/json' },
      });
    }

    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405, headers: CORS });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response('Invalid JSON', { status: 400, headers: CORS });
    }

    const token = env.GH_DISPATCH_TOKEN || '';
    if (!token) {
      return new Response(JSON.stringify({ error: 'GH_DISPATCH_TOKEN secret not set in worker' }), {
        status: 500, headers: { ...CORS, 'Content-Type': 'application/json' },
      });
    }

    const res = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method:  'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Accept':        'application/vnd.github.v3+json',
          'Content-Type':  'application/json',
          'User-Agent':    'vn-trade-proxy',
        },
        body: JSON.stringify(body),
      }
    );

    const text = await res.text();
    return new Response(text || null, {
      status:  res.status,
      headers: { ...CORS, 'Content-Type': 'application/json' },
    });
  },
};
