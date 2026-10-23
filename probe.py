import asyncio, re, json
from playwright.async_api import async_playwright
URLS=["https://www.work.ua/jobs-remote-account+manager/","https://www.work.ua/jobs-remote-%D1%82%D0%B5%D0%BD%D0%B4%D0%B5%D1%80/"]
async def main():
    out={}
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
        ctx=await b.new_context(locale="uk-UA",user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
        pg=await ctx.new_page()
        for u in URLS:
            try:
                r=await pg.goto(u,wait_until="domcontentloaded",timeout=45000)
                for _ in range(15):
                    if "moment" not in (await pg.title()).lower(): break
                    await pg.wait_for_timeout(2000)
                h=await pg.content()
                links=sorted(set(re.findall(r'href="(/jobs/\d+/)"',h)))
                out[u]={"status":r.status if r else None,"title":await pg.title(),"n_links":len(links),"sample":links[:5]}
            except Exception as e: out[u]={"err":str(e)}
        try:
            first=(out[URLS[0]].get("sample") or [None])[0]
            if first:
                await pg.goto("https://www.work.ua"+first,timeout=45000); await pg.wait_for_timeout(3000)
                out["detail"]={"title":await pg.title(),"len":len(await pg.content())}
        except Exception as e: out["detail"]={"err":str(e)}
        await b.close()
    print(json.dumps(out,ensure_ascii=False,indent=1))
asyncio.run(main())
