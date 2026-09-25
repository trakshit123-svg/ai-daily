"""Dump paragraph text of article pages. Usage: python3 scripts/article_text.py <url> [...]"""
import sys,re,urllib.request,html
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
for u in sys.argv[1:]:
    try:
        b=urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":UA}),timeout=25).read().decode('utf-8','ignore')
        ps=re.findall(r'<p[^>]*>(.*?)</p>',b,re.S)
        t=' '.join(html.unescape(re.sub('<[^>]+>','',p)).strip() for p in ps)
        t=re.sub(r'\s+',' ',t)
        print("==",u[:80],"\n",t[:1100],"\n")
    except Exception as e: print("==",u,"ERR",e)
