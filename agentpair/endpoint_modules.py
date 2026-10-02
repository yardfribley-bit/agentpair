"""Server-published, versioned endpoint capabilities; never accept caller code."""
import base64
import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).with_name('endpoint_modules')
CAPABILITIES = {'process_details', 'process_tcp'}


def bundle(module_id, parameters):
    if module_id not in CAPABILITIES:
        raise ValueError('Unknown endpoint capability')
    if not isinstance(parameters, dict) or set(parameters) != {'pid', 'startedAt'}:
        raise ValueError('Target pid and startedAt required')
    if type(parameters['pid']) is not int or parameters['pid'] <= 0:
        raise ValueError('Invalid target pid')
    if not isinstance(parameters['startedAt'], str) or not 1 <= len(parameters['startedAt']) <= 80:
        raise ValueError('Invalid target start time')
    try:
        started = datetime.fromisoformat(parameters['startedAt'].replace('Z', '+00:00'))
        if started.tzinfo is None:
            raise ValueError('Timezone required')
    except ValueError:
        raise ValueError('Invalid target start time') from None
    source = (ROOT / (module_id + '.ps1')).read_bytes()
    return {'protocolVersion': 1, 'id': module_id, 'version': '1.0.0',
            'runtime': 'powershell-5.1', 'permissions': ['target-process-read'],
            'timeoutSeconds': 30, 'maxOutputBytes': 524288,
            'sha256': hashlib.sha256(source).hexdigest(),
            'sourceBase64': base64.b64encode(source).decode(),
            'parameters': json.loads(json.dumps(parameters))}


def prepare(payload):
    if 'module' in payload:
        raise ValueError('Inline caller-supplied modules are not allowed')
    if payload.get('action') != 'run_module':
        return payload
    result = dict(payload)
    result['module'] = bundle(payload.get('moduleId'), payload.get('parameters'))
    return result
