/** Fail-closed protection: only explicit verified full coverage counts as armed. */

export type ProtectionLike = {
  protectionStatus?: string | null
  protectionCoveragePct?: number | null
  cloud_oco_verified?: boolean | null
  protection_mechanism?: string | null
  protectionMechanism?: string | null
  ordType?: string | null
}

export function isProtectionConfirmed(p?: ProtectionLike | null): boolean {
  if (!p || p.cloud_oco_verified === false) return false
  if (String(p.protectionStatus || '').trim().toLowerCase() !== 'fully_protected') return false
  const coverage = p.protectionCoveragePct
  return coverage == null || (Number.isFinite(coverage) && coverage >= 100)
}

export function isPairedConditional(p?: ProtectionLike | null): boolean {
  const mech = String(p?.protectionMechanism || p?.protection_mechanism || p?.ordType || '').toLowerCase()
  return mech === 'paired_conditional'
}

