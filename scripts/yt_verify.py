"""Verify YouTube videos via oEmbed. Usage: python3 scripts/yt_verify.py <videoId> [...]"""
import sys,json,urllib.request,urllib.parse,re
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
res={}
for vid in sys.argv[1:]:
    url="https://www.youtube.com/watch?v="+vid
    try:
        d=json.load(urllib.request.urlopen(urllib.request.Request("https://www.youtube.com/oembed?format=json&url="+urllib.parse.quote(url),headers={"User-Agent":UA}),timeout=20))
        # get upload date + description snippet
        b=urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":UA,"Accept-Language":"en"}),timeout=20).read().decode('utf-8','ignore')
        m=re.search(r'"uploadDate":"([^"]+)"',b) or re.search(r'itemprop="uploadDate" content="([^"]+)"',b) or re.search(r'"publishDate":"([^"]+)"',b)
        desc=re.search(r'"shortDescription":"(.*?)","',b)
        res[vid]=dict(ok=True,title=d['title'],author=d['author_name'],author_url=d['author_url'],date=m.group(1) if m else None,desc=(desc.group(1)[:220] if desc else ''))
    except Exception as e:
        res[vid]=dict(ok=False,err=str(e))
    print(vid,json.dumps(res[vid],ensure_ascii=False))
