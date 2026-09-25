"""Check URLs: HTTP status, <title>, published date. Usage: python3 scripts/check_urls.py urls.txt out.json"""
import sys, re, json, concurrent.futures as cf, urllib.request, ssl
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
pats=[r'"datePublished"\s*:\s*"([^"]+)"', r'article:published_time"\s+content="([^"]+)"', r'content="([^"]+)"\s+property="article:published_time"', r'<time[^>]*datetime="([^"]+)"', r'"dateCreated"\s*:\s*"([^"]+)"',r'name="(?:date|citation_date|dc.date)"\s+content="([^"]+)"']
def chk(u):
    try:
        req=urllib.request.Request(u,headers={"User-Agent":UA,"Accept":"text/html,*/*","Accept-Language":"en"})
        r=urllib.request.urlopen(req,timeout=25,context=ssl.create_default_context())
        b=r.read(600000).decode('utf-8','ignore')
        t=re.search(r'<title[^>]*>(.*?)</title>',b,re.S)
        d=None
        for p in pats:
            m=re.search(p,b)
            if m: d=m.group(1);break
        return u,r.status,(t.group(1).strip()[:90] if t else ''),d
    except Exception as e:
        return u,getattr(e,'code',str(e)[:60]),'',None
urls=[l.strip() for l in open(sys.argv[1]) if l.strip()]
with cf.ThreadPoolExecutor(16) as ex:
    res=list(ex.map(chk,urls))
json.dump(res,open(sys.argv[2],'w'),indent=1)
for u,s,t,d in res: print(s,d,'|',t,'|',u[:100])
