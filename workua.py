"""Збирає вакансії з work.ua справжнім браузером (Cloudflare пропускає після першого виклику).
Результат: data/workua.json — список вакансій зі списку + повний опис зі сторінки вакансії."""
import asyncio, json, re, datetime, urllib.parse, pathlib
from playwright.async_api import async_playwright

QUERIES = ["account manager", "key account manager", "customer success", "менеджер по роботі з клієнтами",
           "ключовими клієнтами", "супровід клієнтів", "тендер", "закупівель", "операційний менеджер",
           "договірної роботи", "retention", "менеджер з розвитку", "бізнес-аналітик", "документообіг",
           "партнерами", "crm"]
LISTS = [f"https://www.work.ua/jobs-remote-{urllib.parse.quote(q.replace(' ', '+'), safe='+')}/" for q in QUERIES]
LISTS += ["https://www.work.ua/jobs-kostopil/", "https://www.work.ua/jobs-rivne-%D0%BC%D0%B5%D0%BD%D0%B5%D0%B4%D0%B6%D0%B5%D1%80/"]
MAX_DETAILS = 120
OUT = pathlib.Path("data/workua.json")

async def open_page(pg, url, tries=3):
    for _ in range(tries):
        try:
            r = await pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            for _ in range(20):
                if "moment" not in (await pg.title()).lower():
                    break
                await pg.wait_for_timeout(1500)
            if r and r.status == 200 or "moment" not in (await pg.title()).lower() and await pg.query_selector("h1"):
                return True
        except Exception:
            pass
        await pg.wait_for_timeout(4000)
    return False

CARDS_JS = """() => [...document.querySelectorAll('h2 a[href^="/jobs/"]')].map(a => {
  const card = a.closest('.card') || a.parentElement.parentElement;
  return {url: 'https://www.work.ua' + a.getAttribute('href'), title: a.textContent.trim(),
          card: card ? card.innerText.replace(/\\s+\\n/g, '\\n').trim().slice(0, 600) : ''};
})"""

DETAIL_JS = """() => {
  const t = s => (document.querySelector(s) || {}).innerText || '';
  return {h1: t('h1'), description: t('#job-description'),
          header: (document.querySelector('#job-description') ? '' : '') +
                  [...document.querySelectorAll('ul.list-unstyled li, .text-indent')].map(e => e.innerText.trim()).join(' | ').slice(0, 1500),
          closed: /вакансі[яю] (в архіві|закрит|неактивн)/i.test(document.body.innerText.slice(0, 4000))};
}"""

async def main():
    started = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    jobs, log = {}, []
    async with async_playwright() as p:
        # справжній Chrome у віконному режимі (xvfb): headless Cloudflare блокує вже з другої сторінки
        ctx = await p.chromium.launch_persistent_context("/tmp/chrome-profile", channel="chrome", headless=False,
                                                         locale="uk-UA", args=["--disable-blink-features=AutomationControlled"])
        await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await open_page(pg, "https://www.work.ua/", tries=2)  # прогрів: тут Cloudflare видає cookie
        for url in LISTS:
            ok = await open_page(pg, url)
            cards = await pg.evaluate(CARDS_JS) if ok else []
            for c in cards:
                c.setdefault("found_by", [])
                jobs.setdefault(c["url"], c)["found_by"].append(url)
            log.append({"list": url, "ok": ok, "cards": len(cards)})
            await pg.wait_for_timeout(3000)
        for i, (u, j) in enumerate(list(jobs.items())[:MAX_DETAILS]):
            if await open_page(pg, u, tries=2):
                j.update(await pg.evaluate(DETAIL_JS)); j["detail_ok"] = True
            else:
                j["detail_ok"] = False
            await pg.wait_for_timeout(2500)
        await ctx.close()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"generated_at": started, "lists": log, "count": len(jobs),
                               "jobs": list(jobs.values())}, ensure_ascii=False, indent=1))
    print(json.dumps(log, ensure_ascii=False, indent=1))
    print("jobs", len(jobs), "details ok", sum(1 for j in jobs.values() if j.get("detail_ok")))

asyncio.run(main())
