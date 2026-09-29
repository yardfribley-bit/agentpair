"""Only a user's saved engineering method authorizes cloud allocation."""
METHODS={
    'local':{'label':'默认协作（不开云机器）','drivers':0},
    'pair':{'label':'云端结对（1 台 Driver）','drivers':1},
    'parallel':{'label':'并行方案探索与评审（2 台 Driver）','drivers':2},
}

def method(value):
    if value not in METHODS:raise ValueError('Unknown engineering method')
    return METHODS[value]
