/**
 * Cloudflare Worker — VN Trade Proxy
 *
 * Receives a trade dispatch request from the web form and forwards it to
 * GitHub Actions using the GH_TOKEN secret stored in CF Worker environment.
 * The browser never sees the token.
 *
 * Deploy:
 *   1. cloudflare.com → Workers & Pages → Create Worker → paste this file → Deploy
 *   2. Worker Settings → Variables and Secrets → Add secret: GH_TOKEN = ghp_...
 *   3. Copy your worker URL and update WORKER_URL in docs/index.html
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
          'Access-Control-Allow-Methods': 'POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type',
        },
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

    const res = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method:  'POST',
        headers: {
          'Authorization': `Bearer ${env.GH_TOKEN}`,
          'Accept':        'application/vnd.github.v3+json',
          'Content-Type':  'application/json',
          'User-Agent':    'vn-trade-proxy',
        },
        body: JSON.stringify(body),
      }
    );

    const text = await res.text();
    return new Response(text || null, { status: res.status, headers: CORS });
  },
};
