/**
 * Folds the flat pipeline event stream into one record per incident_id
 * -- this is what makes "this tool call, this diagnosis, this fix all
 * belong to the same trigger" visible, instead of four unrelated lists.
 */
export function groupIncidents(pipelineEvents) {
  const incidents = new Map()

  for (const e of pipelineEvents) {
    const id = e.incident_id
    if (!id) continue

    if (!incidents.has(id)) {
      incidents.set(id, {
        id,
        incident_event: null,
        severity: null,
        requires_ai: false,
        ai_worthy: false,
        detectedAt: e.timestamp,
        investigatorEvents: [],
        fixerEvents: [],
        status: 'detected',
      })
    }
    const incident = incidents.get(id)

    switch (e.type) {
      case 'incident_detected':
        incident.incident_event = e.incident_event
        incident.severity = e.severity
        incident.requires_ai = e.requires_ai
        incident.ai_worthy = e.ai_worthy
        incident.pattern = e.pattern
        incident.detectedAt = e.timestamp
        break
      case 'ai_analysis_started':
        incident.investigatorEvents.push(e)
        incident.status = 'investigating'
        break
      case 'tool_call':
        incident.investigatorEvents.push(e)
        break
      case 'stage_complete':
        if (e.stage?.startsWith('Investigation')) {
          incident.investigatorEvents.push(e)
          incident.status = 'diagnosed'
        } else if (e.stage?.startsWith('Fix')) {
          incident.fixerEvents.push(e)
          incident.status = 'fix_proposed'
        }
        break
      case 'ai_analysis_result':
        // Not pushed to fixerEvents -- its text is identical to the Fix
        // proposal stage_complete event above (analyze_incident()
        // returns exactly that text), so adding it too would just
        // duplicate the same content under a second card.
        incident.status = 'done'
        break
      case 'ai_analysis_failed':
        incident.fixerEvents.push(e)
        incident.status = 'failed'
        break
      default:
        break
    }
  }

  // Newest first -- a live demo shouldn't require scrolling to see
  // what just happened.
  return Array.from(incidents.values()).sort((a, b) => b.detectedAt - a.detectedAt)
}
