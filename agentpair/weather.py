"""Bounded weather tool; original values and timestamps remain reviewable."""
import datetime
import json
import urllib.parse
import urllib.request

def fetch(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read(200000))

def collect(city):
    if not isinstance(city,str) or not 1<=len(city)<=80:
        raise ValueError('City required')
    geo='https://geocoding-api.open-meteo.com/v1/search?'+urllib.parse.urlencode({'name':city,'count':3,'language':'en'})
    places=fetch(geo).get('results',[])
    if not places: raise ValueError('City not found')
    place=places[0]
    url='https://api.open-meteo.com/v1/forecast?'+urllib.parse.urlencode({
        'latitude':place['latitude'],'longitude':place['longitude'],'timezone':'UTC',
        'current':'temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m',
        'forecast_days':1})
    data=fetch(url); current=data['current']
    stamp=datetime.datetime.fromisoformat(current['time']).replace(tzinfo=datetime.timezone.utc)
    fresh=abs((datetime.datetime.now(datetime.timezone.utc)-stamp).total_seconds())<7200
    return {'tool':'weather','source':'Open-Meteo','sourceUrl':url,'geocodingUrl':geo,
        'retrievedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'place':place,'candidates':places,'current':current,'units':data['current_units'],
        'fresh':fresh,'timezone':'UTC','nature':'模型估算的当前天气，不是气象站实测；单一数据源'}
