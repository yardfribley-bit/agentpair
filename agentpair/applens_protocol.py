"""AppLens v1 capability contract. Unsupported is never an empty success."""
CAPABILITIES={'process_inventory','application_inventory','process_details','process_tcp',
              'process_events','network_events','file_events','cloud_requests'}
STATES={'available','permission_required','unsupported','unavailable'}

def validate_manifest(value):
    if not isinstance(value,dict) or value.get('protocolVersion')!=1 or value.get('product')!='AppLens':
        raise ValueError('Unsupported AppLens protocol')
    caps=value.get('capabilities')
    if not isinstance(caps,dict) or set(caps)!=CAPABILITIES:
        raise ValueError('Incomplete capability manifest')
    if any(state not in STATES for state in caps.values()):
        raise ValueError('Invalid capability state')
    return {'product':'AppLens','protocolVersion':1,'capabilities':dict(caps)}
