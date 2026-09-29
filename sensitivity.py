"""Local-only scanning; cloud packets contain allowlisted metadata, never raw text."""
import json
import re

PATTERNS = {
    'api_token_candidate': re.compile(r'\bsk-[A-Za-z0-9_-]{20,}\b'),
    'aliyun_key_id': re.compile(r'\bLTAI[A-Za-z0-9]{12,}\b'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'credential_assignment_candidate': re.compile(r'(?i)(?:password|passwd|secretkey|api[_-]?key|密码|口令|令牌)\s*[=:：]\s*["\']?[^\s"\',;]{6,}'),
    'bearer_token_candidate': re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._-]{16,}'),
    'email_candidate': re.compile(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b'),
    'cn_mobile_candidate': re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'),
}
MODES = {'completeHTTPSRequestBody', 'preEncryptionRequestBody',
         'preEncryptionRequestProjection', 'agentRecordedContext', 'contextFragment'}


def scan_text(text):
    if not isinstance(text, str):
        return {}
    return {kind: len(list(pattern.finditer(text))) for kind, pattern in PATTERNS.items() if pattern.search(text)}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def build_packet(snapshot):
    if snapshot.get('schemaVersion') not in (1, 4):
        raise ValueError('Unsupported task snapshot schema')
    if not snapshot.get('task', {}).get('taskID'):
        raise ValueError('Missing task identity')
    refs, evidence = {}, []
    def add(locator, record, body, layer):
        categories = scan_text(body)
        ref = f'E{len(refs)+1:04d}'
        refs[ref] = {'locator': locator, 'eventID': record.get('eventID') or record.get('event_id') or record.get('id')}
        mode = record.get('captureMode')
        mode = mode if mode in MODES else 'unknown'
        completeness = record.get('completeness')
        completeness = completeness if completeness in ('complete', 'partial', 'unknown') else 'unknown'
        # Destination is metadata, not proof of transmission or receiver acceptance.
        destination_known = bool(record.get('destination'))
        observed_payload = layer == 'outbound_request' and mode in ('completeHTTPSRequestBody', 'preEncryptionRequestBody')
        assessment = 'sensitive_candidate_present' if categories else 'no_pattern_match'
        if categories and observed_payload:
            assessment = 'sensitive_candidate_in_captured_request_body'
        evidence.append({'ref': ref, 'layer': layer, 'categories': categories,
                         'bodyBytes': len(body.encode('utf-8')) if isinstance(body, str) else 0,
                         'captureMode': mode, 'completeness': completeness,
                         'destinationKnown': destination_known, 'assessment': assessment})
    for section, field, layer in [('outboundRequests', 'body', 'outbound_request'),
                                  ('externalRequests', 'requestBody', 'external_request')]:
        for index, record in enumerate(snapshot.get(section, [])):
            if not isinstance(record, dict): raise ValueError('Invalid evidence record')
            add(f'/{section}/{index}', record, record.get(field, ''), layer)
    for section in ('sourceEvents', 'events'):
        for index, record in enumerate(snapshot.get(section, [])):
            if not isinstance(record, dict): raise ValueError('Invalid event record')
            content = '\n'.join(strings(record))
            if scan_text(content):
                add(f'/{section}/{index}', record, content, 'event_context')
    state = snapshot.get('capture', {}).get('state')
    state = state if state in ('healthy', 'degraded', 'partial', 'unavailable') else 'unknown'
    packet = {'packetVersion': 1, 'taskAlias': 'task-101', 'captureState': state,
              'evidence': evidence, 'rawTextIncluded': False,
              'limitations': ['Pattern matches are candidates, not independently confirmed secrets.',
                              'No pattern match does not mean no sensitive data; private code, business information and other formats remain unassessed.',
                              'Captured plaintext request is not proof of successful delivery or receiver retention.',
                              'Context/transcript alone does not prove network transmission.',
                              'Records across layers may represent the same content; do not add counts as unique disclosures.']}
    # Only constructed allowlisted fields leave the local process. No generic redaction export.
    return packet, refs


def validate_claims(value, packet):
    if not isinstance(value, dict) or not isinstance(value.get('findings'), list):
        raise ValueError('Expected findings array')
    valid = {item['ref'] for item in packet['evidence']}
    for finding in value['findings']:
        if not isinstance(finding, dict): raise ValueError('Invalid finding')
        if finding.get('assessment') not in ('supported', 'contradicted', 'unknown'):
            raise ValueError('Invalid assessment')
        ids = finding.get('evidenceRefs')
        if not isinstance(ids, list) or not ids or not all(isinstance(i,str) and i in valid for i in ids):
            raise ValueError('Unknown or missing evidence reference')
    return value
