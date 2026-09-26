/** First-mile onboarding (2026-09): shown only while account data is unreadable */
export const enDashFirstRun = {
  title: 'Connect this system to your own OKX',
  badge: 'Not ready',
  lead: 'No account data has been read yet. Complete the four steps below and the dashboard will start updating.',
  steps: [
    'Get an OKX account — if you do not have one, use a sign-up channel below (new and existing users both qualify).',
    'Create an API key on OKX with Read and Trade permissions; note the key, secret and passphrase.',
    'Enter the three credentials in the admin account-access page and save (or put them in .env).',
    'Come back and refresh: once the top-bar status reads connected, data starts flowing.',
  ],
  channelsTitle: 'No account yet? Sign-up channels',
  openChannel: 'Open sign-up page',
  ctaAccount: 'Enter API key',
  ctaDocs: 'Deploy & troubleshooting docs',
  ctaRepo: 'Official repo',
  ctaRefresh: 'Check again',
  disclosure: 'This program is free and charges you nothing. The author earns exchange rebates: signing up through the channels above means the exchange shares part of your trading fees with the author. Your own fee rate is unaffected, and orders carry the author\'s broker tag.',
};
