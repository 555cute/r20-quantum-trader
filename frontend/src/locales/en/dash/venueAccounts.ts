/** US-005 · Tri-venue symmetric account cards (environment-first; unknown is never 0) */
export const enVenueAccounts = {
  title: 'Venue Accounts',
  desc: 'Pick the funding environment first, then read real per-venue accounts; unreadable values show "—" and are never faked as 0; live and demo are never summed',
  envLabel: 'Funding environment',
  envDemo: 'Demo',
  envLive: 'Live',
  refresh: 'Refresh',
  loading: 'Syncing…',
  needsAuth: 'Sign in to view real account data',
  captured: 'Captured',
  unknown: '—',
  venueNames: {
    okx: 'OKX',
    gate: 'Gate.io',
    binance: 'Binance',
  },
  status: {
    ready: 'Synced',
    unavailable: 'Not connected',
    not_implemented: 'Not implemented',
    degraded: 'Read failed',
    unknown: 'Unknown',
  },
  fields: {
    equity: 'Equity (USDT)',
    available: 'Available margin',
    positions: 'Positions',
    openOrders: 'Open orders',
    lastSync: 'Last sync',
    unitN: '{n}',
  },
  listing: {
    label: 'Contracts',
    ok: 'Checked · {n}',
    unavailable: 'Directory unavailable · reconciliation skipped',
  },
};
