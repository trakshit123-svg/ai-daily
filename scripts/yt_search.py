"""Newest-first YouTube search (scrapes ytInitialData). Usage: python3 scripts/yt_search.py "query" ["query" ...]"""
import sys, re, json, urllib.request, urllib.parse
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
def search(q, n=6):
    u="https://www.youtube.com/results?search_query="+urllib.parse.quote(q)+"&sp=CAI%253D"  # sort by upload date
    req=urllib.request.Request(u,headers={"User-Agent":UA,"Accept-Language":"en-US,en","Cookie":"CONSENT=YES+1"})
    b=urllib.request.urlopen(req,timeout=25).read().decode('utf-8','ignore')
    m=re.search(r'var ytInitialData = (\{.*?\});</script>',b,re.S)
    if not m: return []
    d=json.loads(m.group(1))
    out=[]
    def walk(x):
        if isinstance(x,dict):
            if 'videoRenderer' in x:
                v=x['videoRenderer']
                t=''.join(r.get('text','') for r in v.get('title',{}).get('runs',[]))
                ch=''.join(r.get('text','') for r in v.get('ownerText',{}).get('runs',[]))
                pub=v.get('publishedTimeText',{}).get('simpleText','')
                out.append((v['videoId'],t,ch,pub))
            for vv in x.values(): walk(vv)
        elif isinstance(x,list):
            for vv in x: walk(vv)
    walk(d)
    return out[:n]
for q in sys.argv[1:]:
    print("==",q)
    try:
        for r in search(q): print("  ",r)
    except Exception as e: print("  ERR",e)
