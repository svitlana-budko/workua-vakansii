"""Збирає вакансії з work.ua справжнім браузером (Cloudflare пропускає після першого виклику).
Результат: data/workua.json — список вакансій зі списку + повний опис зі сторінки вакансії."""
import asyncio, json, re, datetime, urllib.parse, pathlib, random, tempfile
from playwright.async_api import async_playwright

import sys

def remote(qs):
    return [f"https://www.work.ua/jobs-remote-{urllib.parse.quote(q.replace(' ', '+'), safe='+')}/" for q in qs]

# Профілі: maryna — робота з клієнтами, тендери, операційка (+ Костопіль і Рівне);
# svitlana — керівник продажів, growth, RevOps, навчання продажників (лише віддалено).
PROFILES = {
    "maryna": (remote(["account manager", "key account manager", "customer success", "менеджер по роботі з клієнтами",
                       "ключовими клієнтами", "супровід клієнтів", "тендер", "закупівель", "операційний менеджер",
                       "договірної роботи", "retention", "менеджер з розвитку", "бізнес-аналітик", "документообіг",
                       "партнерами", "crm"])
               + ["https://www.work.ua/jobs-kostopil/", "https://www.work.ua/jobs-rivne-%D0%BC%D0%B5%D0%BD%D0%B5%D0%B4%D0%B6%D0%B5%D1%80/"],
               "data/workua.json"),
    "svitlana": (remote(["head of sales", "керівник відділу продажів", "комерційний директор", "директор з продажу",
                         "sales director", "sales team lead", "growth", "revops", "sales operations", "retention",
                         "crm маркетолог", "customer success lead", "тренер з продажу", "керівник відділу по роботі з клієнтами",
                         "chief revenue officer", "e-commerce manager"]),
                 "data/workua_svitlana.json"),
}
PROFILE = sys.argv[1] if len(sys.argv) > 1 else "maryna"
LISTS, OUT_NAME = PROFILES[PROFILE]
MAX_DETAILS = 150
MAX_RESTARTS = 6
OUT = pathlib.Path(OUT_NAME)

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

class Browser:
    """Справжній Chrome у віконному режимі (xvfb). Коли Cloudflare починає блокувати,
    браузер перезапускається з чистим профілем — свіжа сесія зазвичай знову проходить."""
    def __init__(self, p):
        self.p, self.ctx, self.pg, self.restarts = p, None, None, 0

    async def start(self):
        if self.ctx:
            await self.ctx.close()
        self.ctx = await self.p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="chrome-"), channel="chrome", headless=False, locale="uk-UA",
            args=["--disable-blink-features=AutomationControlled"])
        await self.ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self.pg = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        await open_page(self.pg, "https://www.work.ua/", tries=2)  # прогрів: тут Cloudflare видає cookie

    async def get(self, url):
        if await open_page(self.pg, url, tries=2):
            return True
        if self.restarts >= MAX_RESTARTS:
            return False
        self.restarts += 1
        await asyncio.sleep(random.uniform(20, 40))
        await self.start()
        return await open_page(self.pg, url, tries=2)

    async def pause(self, lo, hi):
        await self.pg.wait_for_timeout(int(random.uniform(lo, hi) * 1000))


def load_previous():
    try:
        return json.loads(OUT.read_text())
    except Exception:
        return None


def ok_share(d):
    return sum(1 for l in d["lists"] if l["ok"]) / max(len(d["lists"]), 1) if d else 0


async def main():
    started = datetime.datetime.utcnow()
    prev = load_previous()
    if prev and "--force" not in sys.argv and prev["generated_at"][:10] == started.date().isoformat() and ok_share(prev) >= 0.9:
        print("сьогоднішній файл уже повний — пропускаю"); return
    # описи, які вже відкривали за останні 3 дні, не тягнемо вдруге — менше запитів, менше блокувань
    known = {}
    if prev and (started - datetime.datetime.fromisoformat(prev["generated_at"].rstrip("Z"))).days < 3:
        known = {j["url"]: j for j in prev["jobs"] if j.get("detail_ok")}
    jobs, log = {}, []
    async with async_playwright() as p:
        b = Browser(p)
        await b.start()
        for url in LISTS:
            ok = await b.get(url)
            cards = await b.pg.evaluate(CARDS_JS) if ok else []
            for c in cards:
                c.setdefault("found_by", [])
                jobs.setdefault(c["url"], c)["found_by"].append(url)
            log.append({"list": url, "ok": ok, "cards": len(cards)})
            await b.pause(4, 8)
        fetched = 0
        for u, j in jobs.items():
            if u in known:
                j.update({k: known[u][k] for k in ("h1", "description", "header", "closed", "detail_ok") if k in known[u]})
                continue
            if fetched >= MAX_DETAILS:
                j["detail_ok"] = False
                continue
            fetched += 1
            if await b.get(u):
                j.update(await b.pg.evaluate(DETAIL_JS)); j["detail_ok"] = True
            else:
                j["detail_ok"] = False
            await b.pause(2.5, 5)
        await b.ctx.close()
    new = {"generated_at": started.isoformat(timespec="seconds") + "Z", "lists": log, "count": len(jobs),
           "restarts": b.restarts, "jobs": list(jobs.values())}
    print(json.dumps(log, ensure_ascii=False, indent=1))
    print("jobs", len(jobs), "details ok", sum(1 for j in jobs.values() if j.get("detail_ok")), "restarts", b.restarts)
    # гірший прохід не затирає кращий свіжий: агент краще прочитає вчорашній повний файл, ніж сьогоднішні уламки
    if prev and ok_share(new) < ok_share(prev) and ok_share(new) < 0.6 and \
            (started - datetime.datetime.fromisoformat(prev["generated_at"].rstrip("Z"))).total_seconds() < 36 * 3600:
        prev["last_failed_attempt"] = new["generated_at"]
        OUT.write_text(json.dumps(prev, ensure_ascii=False, indent=1))
        print("прохід гірший за попередній — залишаю попередній файл"); return
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(new, ensure_ascii=False, indent=1))

asyncio.run(main())
