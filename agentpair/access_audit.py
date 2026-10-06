"""Bounded, read-only HTTP access summaries. No cookies, query strings or bodies."""
import datetime
from pathlib import Path
import re

PATTERN=re.compile(r'^(\S+) .*?\[([^]]+)\] "(\S+) ([^ ?]+)[^"]*" (\d+)')


def recent_access(path='/var/log/nginx/access.log', minutes=10, clock=None):
    if type(minutes) is not int or not 1<=minutes<=60:
        raise ValueError('访问查询时间范围必须为1至60分钟')
    now=clock or datetime.datetime.now(datetime.timezone.utc)
    groups={}
    with Path(path).open('rb') as stream:
        stream.seek(0,2);size=stream.tell();start=max(0,size-2*1024*1024)
        stream.seek(start)
        if start:stream.readline()
        lines=stream.read(2*1024*1024).decode('utf-8',errors='replace').splitlines()
    for line in lines:
        match=PATTERN.match(line)
        if not match:continue
        ip,stamp,method,url,status=match.groups()
        # This server also hosts a proxy service; those connections aren't platform visits.
        if url.startswith('/vmess') or url.startswith('/cdn-cgi/'):continue
        try:at=datetime.datetime.strptime(stamp,'%d/%b/%Y:%H:%M:%S %z')
        except ValueError:continue
        if not 0<=(now-at).total_seconds()<=minutes*60:continue
        row=groups.setdefault(ip,{'name':ip,'ip':ip,'requests':0,'lastAccess':at.isoformat(),
                                  'visitType':'服务器本机来源，不能确认真实用户' if ip in ('127.0.0.1','::1','50.118.187.180') else 'HTTP访问来源',
                                  'account':'未关联账号'})
        row['requests']+=1
        row['lastAccess']=max(row['lastAccess'],at.isoformat())
    return {'items':sorted(groups.values(),key=lambda r:r['lastAccess'],reverse=True)[:50],
            'windowMinutes':minutes,'checkedAt':now.isoformat(),'truncated':start>0,
            'basis':'最近HTTP请求；不代表仍在线，IP不等于具体人员。查询最多读取当前日志末尾2MB。'}
