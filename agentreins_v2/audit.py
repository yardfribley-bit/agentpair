"""Coverage assessment, deliberately not a completeness certification."""
from collections import Counter


def audit(store):
    counts, gaps, requests, after = Counter(), [], 0, 0
    while True:
        batch = store.events(after, limit=1000)
        if not batch:
            break
        for row in batch:
            after = row['seq']
            counts[row['kind']] += 1
            if row['kind'] == 'coverage.gap':
                gaps.append({'event_id': row['event_id'], 'reason': row.get('reason'), 'source': row['source']})
            if row['kind'] == 'model.request':
                try:
                    body = store.content(row['content_ref']['sha256'])
                    if isinstance(body.get('messages'), list) and len(body['messages']) == row['message_count']:
                        requests += 1
                except (OSError, ValueError, KeyError, AttributeError):
                    pass
    return {'event_counts': dict(counts), 'reconstructed_requests': requests,
            'stored_requests': counts['model.request'], 'coverage_gaps': gaps,
            'all_model_requests_captured': 'unknown',
            'explanation': 'Stored request reconstruction checks content integrity, not interception completeness. Trace generations are not a one-to-one denominator for HTTP requests.',
            'network_coverage': 'periodic scoped socket snapshots; transient connections may be missed' if counts['network.sample'] else 'not_sampled',
            'tool_to_socket_correlation': 'not_observed'}
