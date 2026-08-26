#!/usr/bin/env python3
import json, re
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin
from html.parser import HTMLParser

ROOT=Path(__file__).resolve().parents[1]
CFG=json.loads((ROOT/'future-sources.json').read_text(encoding='utf-8'))
OUT=ROOT/'future-data.json'
UA='Mozilla/5.0 (compatible; BlueArchiveFutureBot/1.0)'

class Parser(HTMLParser):
    def __init__(self): super().__init__(); self.text=[]; self.links=[]; self.a=None
    def handle_starttag(self,t,a):
        d=dict(a)
        if t=='a' and d.get('href'):
            self.a={'href':d['href'],'text':[]}; self.links.append(self.a)
    def handle_endtag(self,t):
        if t=='a': self.a=None
    def handle_data(self,d):
        s=' '.join(d.split())
        if s:
            self.text.append(s)
            if self.a is not None: self.a['text'].append(s)

def fetch(url):
    r=urlopen(Request(url,headers={'User-Agent':UA,'Accept-Language':'ko-KR,ko;q=0.9'}),timeout=25)
    enc=r.headers.get_content_charset() or 'utf-8'
    return r.read().decode(enc,errors='replace')

def parse(url):
    p=Parser(); p.feed(fetch(url)); return p

def dates(s):
    out=[]
    for y,m,d in re.findall(r'(20\\d{2})[.\\-/](\\d{1,2})[.\\-/](\\d{1,2})',s):
        out.append(f'{int(y):04d}-{int(m):02d}-{int(d):02d}')
    return sorted(set(out))

def mollulog():
    p=parse('https://mollulog.net/futures')
    rows=[]; current=None; buf=[]
    for line in p.text:
        ds=dates(line)
        if ds:
            if current and buf: rows.append({'date':current,'text':' '.join(buf)})
            current=ds[0]; buf=[]
        elif current: buf.append(line)
    if current and buf: rows.append({'date':current,'text':' '.join(buf)})
    return {'sourceId':'mollulog','kind':'estimate','checkedAt':datetime.now().isoformat(),'items':rows[:300]}

def community(src):
    p=parse(src['url']); keys=['미래시','픽업','모집','이벤트','공략','정보','한섭','일섭','총력전','대결전']
    items=[]; seen=set()
    for a in p.links:
        title=' '.join(a['text']).strip(); u=urljoin(src['url'],a['href'])
        if title and any(k in title for k in keys) and u not in seen:
            seen.add(u); items.append({'title':title,'url':u})
    return {'sourceId':src['id'],'kind':src['kind'],'checkedAt':datetime.now().isoformat(),'items':items[:100]}

def main():
    checks=[]; errors=[]
    for s in CFG['sources']:
        if not s.get('enabled'): continue
        try:
            checks.append(mollulog() if s['id']=='mollulog' else community(s))
        except Exception as e:
            errors.append({'sourceId':s['id'],'error':str(e)})
    old={}
    if OUT.exists():
        try: old=json.loads(OUT.read_text(encoding='utf-8'))
        except: pass
    result={
      'updatedAt':datetime.now().astimezone().isoformat(timespec='seconds'),
      'server':'KR','defaultRangeMonths':4,'supportedRangeMonths':[2,4,6,12],
      'events':old.get('events',[]),
      'sourceEvidence':checks,'errors':errors,
      'rules':CFG['autoUpdate']['validation'],
      'note':'몰루로그는 예상 일정, 공식 자료는 확정 일정으로 분리. 자료 충돌은 의견 갈림으로 처리.'
    }
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__': main()
