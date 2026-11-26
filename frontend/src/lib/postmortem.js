/**
 * Build and download a structured Markdown postmortem report for an incident.
 */
export function buildPostmortemMarkdown({ incident, result, causalCandidates = [], files = [] }) {
  const lines = []
  const now = new Date().toISOString()
  const conf = Number.isFinite(result?.confidence_score)
    ? `${Math.round(result.confidence_score * 100)}% (${result.confidence || 'unknown'})`
    : result?.confidence || 'N/A'

  lines.push(`# Incident Postmortem: ${incident?.name || 'Untitled Incident'}`)
  lines.push(`- **Generated at:** ${now}`)
  lines.push(`- **Decision:** ${result?.decision || 'unverified'}`)
  lines.push(`- **Confidence:** ${conf}`)
  if (result?.suspected_origin) {
    lines.push(`- **Suspected Origin:** \`${result.suspected_origin}\``)
  }
  if (result?.affected_services?.length) {
    lines.push(`- **Affected Services:** ${result.affected_services.map((s) => `\`${s}\``).join(', ')}`)
  }
  lines.push(`- **Telemetry Files:** ${files.length ? files.join(', ') : 'None'}`)
  lines.push('')

  if (result?.root_cause || result?.summary) {
    lines.push('## Summary & Root Cause')
    if (result.root_cause) lines.push(`**Root Cause:** ${result.root_cause}`)
    if (result.summary) lines.push('', result.summary)
    lines.push('')
  }

  if (result?.evidence?.length) {
    lines.push('## Verified Evidence')
    for (const ev of result.evidence) {
      const ts = ev.timestamp ? `[${ev.timestamp}] ` : ''
      lines.push(`- ${ts}**${ev.type}** (\`${ev.source}\`): ${ev.why || ev.snippet}`)
    }
    lines.push('')
  }

  if (result?.hypotheses?.length) {
    lines.push('## Hypothesis Verification Checks')
    for (const h of result.hypotheses) {
      lines.push(`### ${h.label} (\`${h.service}\`, score: ${h.score})`)
      for (const c of h.checks || []) {
        lines.push(`- **[${c.outcome.toUpperCase()}]** ${c.criterion}: ${c.detail}`)
      }
      lines.push('')
    }
  }

  if (causalCandidates.length) {
    lines.push('## Unsupervised Causal Ranking')
    causalCandidates.forEach((c, idx) => {
      lines.push(
        `${idx + 1}. **${c.service}** — Score ${Math.round(c.score * 100)}% (Temporal ${c.temporal_score.toFixed(2)}, Upstream ${c.upstream_score.toFixed(2)}, Modal ${c.modal_score.toFixed(2)})`,
      )
    })
    lines.push('')
  }

  if (result?.recommendations?.length) {
    lines.push('## Recommendations')
    for (const rec of result.recommendations) {
      lines.push(`- ${rec}`)
    }
    lines.push('')
  }

  return lines.join('\n')
}

export function downloadPostmortem(params) {
  const md = buildPostmortemMarkdown(params)
  const slug = (params.incident?.name || 'incident')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '') || 'incident'
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${slug}-postmortem.md`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

