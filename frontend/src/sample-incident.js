export const SAMPLE_ID = 'sample-incident'

export const sampleIncident = {
  id: SAMPLE_ID,
  name: 'Authentication timeouts',
  files: ['demo_app.log', 'demo_metrics.json', 'demo_dashboard.png'],
}

export const sampleResult = {
  root_cause: 'Database connection pool exhausted',
  summary: 'The user-db pool reached its 20-connection limit. Authentication requests stalled while waiting for a connection, then the API gateway returned 504s.',
  confidence: 'high',
  confidence_score: 0.96,
  suspected_origin: 'user-db',
  affected_services: ['user-db', 'auth-service', 'api-gateway'],
  evidence: [
    { type: 'log', source: 'demo_app.log', snippet: '02:31:00 UTC · user-db · PoolExhaustedError\nConnection acquisition failed. Active: 20. Limit: 20.', score: 0.94 },
    { type: 'metrics', source: 'demo_metrics.json', snippet: '02:31:00 UTC · 20 active database connections\nRequest error rate reached 89.3%.', score: 0.89 },
    { type: 'log', source: 'demo_app.log', snippet: '02:31:02 UTC · api-gateway · HTTP 504\nPOST /login timed out waiting for auth-service.', score: 0.82 },
  ],
  timeline: [
    '02:30:30 · Connection wait increases while the pool is at its 20-connection limit.',
    '02:31:00 · The user-db pool reaches its connection limit.',
    '02:31:02 · Gateway returns 504 for the login request.',
    '02:32:45 · Login requests begin succeeding again.',
  ],
  recommendations: [
    'Check for long-held connections before increasing the pool limit.',
    'Add an alert on pool saturation and connection acquisition wait.',
  ],
  agent_trace: [
    { agent: 'Retrieval', action: 'Collect relevant observations', result: 'Selected the database metric and two log entries from the incident window.' },
    { agent: 'RCA', action: 'Connect the incident events', result: 'Pool saturation preceded the authentication timeout and gateway error.' },
  ],
  hypotheses: [
    {
      label: 'Connection pool limit reduction',
      service: 'user-db',
      score: 4,
      checks: [
        { criterion: 'Recorded limit reduction', outcome: 'supports', detail: 'Pool limit reduced from 100 to 20 at 02:29:15 UTC.' },
        { criterion: 'Pre-change baseline', outcome: 'supports', detail: 'Active connections were 15/100 with 17.2 ms latency before the change.' },
        { criterion: 'Pool saturation and latency', outcome: 'supports', detail: 'Active connections reached 20/20 as latency surged to 30,045 ms.' },
        { criterion: 'Downstream timeout', outcome: 'supports', detail: 'auth-service reported DatabaseTimeout after saturation.' },
      ],
    },
    {
      label: 'CPU pressure',
      service: 'user-db',
      score: 0,
      checks: [
        { criterion: 'Pre-spike CPU baseline', outcome: 'supports', detail: 'CPU was 42.8% before the incident.' },
        { criterion: 'Elevated CPU', outcome: 'missing', detail: 'Peak CPU reached 72.4%, below the 80% threshold.' },
      ],
    },
  ],
}

export const sampleGraph = {
  nodes: [
    { id: 'user-db', type: 'database' },
    { id: 'auth-service', type: 'service' },
    { id: 'api-gateway', type: 'service' },
    { id: 'demo_app.log', type: 'file' },
  ],
  edges: [
    { source: 'api-gateway', target: 'auth-service', rel: 'depends_on' },
    { source: 'auth-service', target: 'user-db', rel: 'depends_on' },
    { source: 'demo_app.log', target: 'user-db', rel: 'mentions' },
  ],
}

export const samplePrompts = [
  {
    topic: 'Explain the cause',
    label: 'What caused the outage?',
    reply: sampleResult.summary,
    references: [{ view: 'evidence', index: 0, label: 'Pool exhaustion log' }, { view: 'evidence', index: 1, label: 'Connection metrics' }],
  },
  {
    topic: 'Assess the impact',
    label: 'Which services were affected?',
    reply: 'user-db reached its connection limit, auth-service stalled waiting for a connection, and api-gateway returned 504s on login requests.',
    references: [{ view: 'evidence', index: 2, label: 'Gateway 504 log' }, { view: 'graph', node: 'api-gateway', label: 'Gateway in graph' }],
  },
  {
    topic: 'Find the next check',
    label: 'What should I check next?',
    reply: 'Start by checking connection hold times and whether the pool has normalized. Look for slow queries or leaked connections before changing the limit.',
    references: [{ view: 'evidence', index: 1, label: 'Connection metrics' }],
  },
  {
    topic: 'Draft a handoff',
    label: 'Draft an incident handoff.',
    reply: 'Impact: Login requests returned 504s around 02:31 UTC.\nObserved cause: user-db reached its 20-connection limit.\nRecovery: Login requests resumed at 02:32:45 UTC in the sample logs.\nNext check: Confirm the pool has normalized and inspect long-held connections.',
    references: [{ view: 'evidence', index: 0, label: 'Pool exhaustion log' }, { view: 'evidence', index: 2, label: 'Gateway 504 log' }, { view: 'timeline', index: 3, label: 'Login recovery event' }],
  },
]

const sampleActivity = [
  ['02:30:30', '02:31:00', { all: 1, anomalies: 1 }],
  ['02:31:00', '02:31:30', { all: 2, errors: 2 }],
  ['02:31:30', '02:32:00', { all: 0 }],
  ['02:32:00', '02:32:30', { all: 0 }],
  ['02:32:30', '02:33:00', { all: 1 }],
]
const sampleTime = time => `2024-01-15T${time}Z`

export const sampleOverview = {
  counts: { all: 4, errors: 2, anomalies: 1, changes: 0 },
  modalities: { log: 3, metrics: 1 },
  services: sampleResult.affected_services,
  first_at: sampleTime('02:30:30'),
  last_at: sampleTime('02:32:45'),
  undated: 0,
  activity: sampleActivity.map(([start, end, counts]) => ({
    start: sampleTime(start), end: sampleTime(end),
    counts: { errors: 0, anomalies: 0, changes: 0, ...counts },
  })),
}
