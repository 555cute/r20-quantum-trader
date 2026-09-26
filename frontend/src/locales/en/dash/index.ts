import { enDashFirstRun } from './firstRun';
import { enShell } from './shell';
import { enMatrix } from './matrix';
import { enRadar } from './radar';
import { enNews } from './news';
import { enEvolution } from './evolution';
import { enLedger } from './ledger';
import { enAbout } from './about';
import { enVenueAccounts } from './venueAccounts';

export const enDash = {
  shell: enShell,
  matrix: enMatrix,
  radar: enRadar,
  news: enNews,
  evolution: enEvolution,
  ledger: enLedger,
  firstRun: enDashFirstRun,
  about: enAbout,
  venueAccounts: enVenueAccounts,
};
