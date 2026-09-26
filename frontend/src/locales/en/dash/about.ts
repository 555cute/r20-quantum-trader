export const enAbout = {
  title: 'About AstraQuant',
  desc: 'Version, licence and source repository',
  arch: {
    title: 'Architecture',
    stack: 'FastAPI + Vue 3 static SPA',
    points: [
      'LLM holds full decision authority; a Python base hard-blocks violations',
      '100% exchange-side cloud OCO stops, Fail-Closed by design',
      'Multi-model council debate + heuristic self-evolving memory loop',
    ],
  },
  repo: { title: 'Open-source repo', visit: 'Visit repo', starHint: 'Stars & issues welcome' },
  community: {
    title: 'Community',
    qqGroup: 'Quant QQ group',
    qqPersonal: 'Author QQ',
    linuxdo: 'LINUX DO',
    // 2026-09: the channel list is served by the backend
    // (`/api/v1/referral-channels`, public read-only); only the label stays here.
    channel: '{venue} channel',
    open: 'Open sign-up',
    copyHint: 'Click to copy',
  },
  version: 'Version {v} · build {r}',
  license: 'MIT License · For research only, not financial advice',
  risk: 'Risk warning: crypto perpetuals carry extreme leverage risk. Past performance never guarantees future results.',
};
