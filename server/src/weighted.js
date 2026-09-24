// Shared weighted Buy/Hold/Sell calculation - Fundamental and Technical
// computed independently: sum the % of AUM of matched holdings by verdict,
// divided by the total % of AUM among matched holdings that actually carry
// a verdict for that field, so the three shares always sum to 100%.
function weightedVerdict(holdings, field) {
  let total = 0;
  const sums = { Buy: 0, Hold: 0, Sell: 0 };
  for (const h of holdings) {
    if (!h.matched_company_id && !h.matchedCompanyId) continue;
    const v = field === 'fundamental' ? h.fundamental : h.technical;
    if (!v || !(v in sums)) continue;
    sums[v] += Number(h.pct_aum ?? h.pctAum);
    total += Number(h.pct_aum ?? h.pctAum);
  }
  if (total <= 0) return null;
  return {
    total,
    buy: (sums.Buy / total) * 100,
    hold: (sums.Hold / total) * 100,
    sell: (sums.Sell / total) * 100,
  };
}

module.exports = { weightedVerdict };
