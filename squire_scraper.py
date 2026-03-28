# ============================================================
# SQUIRE — LIVE CRE INTELLIGENCE AGENT
# Batman Robb Report · Multi-Source CRE Pipeline
# Sources: CoStar | LoopNet | Crexi
# ============================================================

import os
import asyncio
import argparse
import json
import uuid
import re
import random
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
from dotenv import load_dotenv

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page
from tenacity import retry, stop_after_attempt, wait_exponential
from rich.console import Console
from rich.table import Table

load_dotenv()
console = Console()

# ─── CONFIG ──────────────────────────────────────────────────
SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY", "")
COSTAR_USER    = os.getenv("COSTAR_USERNAME", "")
COSTAR_PASS    = os.getenv("COSTAR_PASSWORD", "")
CREXI_API_KEY  = os.getenv("CREXI_API_KEY", "")
MIN_CAP_RATE   = float(os.getenv("MIN_CAP_RATE", 0.05))
MIN_DSCR       = float(os.getenv("MIN_DSCR", 1.25))
TARGET_MARKETS = [m.strip() for m in os.getenv("TARGET_MARKETS", "Miami,New York City,Dallas").split(",")]
DEBT_CONSTANT  = 0.065  # ~6.5% all-in debt cost for DSCR estimate

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

# ─── UNIFIED LISTING SCHEMA ───────────────────────────────────
@dataclass
class CREListing:
    listing_id:    str   = field(default_factory=lambda: f"LST-{str(uuid.uuid4())[:8].upper()}")
    source:        str   = "unknown"
    title:         str   = ""
    asset_type:    str   = ""
    market:        str   = ""
    address:       str   = ""
    asking_price:  float = 0.0
    noi:           float = 0.0
    cap_rate:      float = 0.0
    sqft:          float = 0.0
    lot_size:      str   = ""
    year_built:    int   = 0
    url:           str   = ""
    raw:           Dict  = field(default_factory=dict)
    scraped_at:    str   = field(default_factory=lambda: datetime.now().isoformat())
    qualified:     bool  = False
    qualify_score: float = 0.0

    def qualify(self) -> bool:
        estimated_dscr = self.cap_rate / DEBT_CONSTANT if DEBT_CONSTANT else 0
        score = (self.cap_rate * 100) + (estimated_dscr * 15)
        self.qualify_score = round(score, 3)
        self.qualified = (
            self.cap_rate >= MIN_CAP_RATE and
            estimated_dscr >= MIN_DSCR and
            self.asking_price > 0
        )
        return self.qualified

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "raw"}


# ─── HELPERS ─────────────────────────────────────────────────
def _parse_price(text: str) -> float:
    if not text:
        return 0.0
    text = re.sub(r"[^\d.,KMBkmb]", "", text)
    multipliers = {"K": 1e3, "M": 1e6, "B": 1e9, "k": 1e3, "m": 1e6, "b": 1e9}
    for suffix, mult in multipliers.items():
        if suffix in text:
            return float(text.replace(suffix, "").replace(",", "") or 0) * mult
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return 0.0

def _parse_cap_rate(text: str) -> float:
    if not text:
        return 0.0
    match = re.search(r"[\d.]+", text)
    if match:
        val = float(match.group())
        return val / 100 if val > 1 else val
    return 0.0

def proxy_url(target_url: str, render_js: bool = False) -> str:
    params = f"api_key={SCRAPERAPI_KEY}&url={target_url}"
    if render_js:
        params += "&render=true"
    return f"http://api.scraperapi.com?{params}"


# ─── SOURCE 1: CREXI ─────────────────────────────────────────
class CrexiAgent:
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def fetch_listings(self, market: str, asset_type: str = "office", limit: int = 20) -> List[CREListing]:
        listings = []
        if CREXI_API_KEY:
            listings = await self._fetch_api(market, asset_type, limit)
        if not listings:
            listings = await self._scrape_fallback(market, asset_type, limit)
        return listings

    async def _fetch_api(self, market: str, asset_type: str, limit: int) -> List[CREListing]:
        headers = {"Authorization": f"Bearer {CREXI_API_KEY}", "Content-Type": "application/json"}
        params  = {"location": market, "propertyType": asset_type, "transactionType": "sale",
                   "limit": limit, "sort": "capRate_desc"}
        listings = []
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get("https://api.crexi.com/v1/listings", headers=headers, params=params)
                resp.raise_for_status()
                for item in resp.json().get("results", []):
                    lst = CREListing(
                        source="Crexi",
                        title=item.get("name", ""),
                        asset_type=item.get("propertyType", asset_type),
                        market=market,
                        address=item.get("address", {}).get("full", ""),
                        asking_price=float(item.get("askingPrice", 0)),
                        noi=float(item.get("noi", 0)),
                        cap_rate=float(item.get("capRate", 0)),
                        sqft=float(item.get("buildingSize", 0)),
                        year_built=int(item.get("yearBuilt", 0) or 0),
                        url=f"https://www.crexi.com/properties/{item.get('id', '')}",
                        raw=item,
                    )
                    listings.append(lst)
                console.print(f"[green][CREXI API][/green] {len(listings)} listings — {market}")
            except Exception as e:
                console.print(f"[yellow][CREXI API warn][/yellow] {e}")
        return listings

    async def _scrape_fallback(self, market: str, asset_type: str, limit: int) -> List[CREListing]:
        market_slug = market.lower().replace(" ", "-")
        url = f"https://www.crexi.com/properties?location={market_slug}&propertyType={asset_type}&transactionType=sale"
        listings = []
        async with httpx.AsyncClient(timeout=45) as client:
            try:
                resp = await client.get(proxy_url(url, render_js=True))
                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.select("[data-testid='property-card'], .property-card, .listing-card")
                for card in cards[:limit]:
                    title = card.select_one("h2, h3, .title, .property-name")
                    price = card.select_one(".price, .asking-price, [class*='price']")
                    cap   = card.select_one("[class*='cap-rate'], [class*='capRate']")
                    link  = card.select_one("a[href]")
                    lst = CREListing(
                        source="Crexi (scraped)",
                        title=title.get_text(strip=True) if title else "Unknown",
                        market=market, asset_type=asset_type,
                        asking_price=_parse_price(price.get_text(strip=True)) if price else 0.0,
                        cap_rate=_parse_cap_rate(cap.get_text(strip=True)) if cap else 0.0,
                        url="https://www.crexi.com" + link["href"] if link else "",
                    )
                    listings.append(lst)
                console.print(f"[green][CREXI SCRAPE][/green] {len(listings)} listings — {market}")
            except Exception as e:
                console.print(f"[red][CREXI SCRAPE ERROR][/red] {e}")
        return listings


# ─── SOURCE 2: LOOPNET (PLAYWRIGHT) ──────────────────────────
class LoopNetAgent:
    SEARCH_URL = "https://www.loopnet.com/search/commercial-real-estate/{market}/for-sale/"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=15))
    async def fetch_listings(self, market: str, asset_type: str = "office", limit: int = 20) -> List[CREListing]:
        market_slug = market.lower().replace(",", "").replace(" ", "-")
        url = self.SEARCH_URL.format(market=market_slug)
        listings = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"]
            )
            ctx = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1440, "height": 900},
                locale="en-US",
            )
            await ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
                "window.chrome = { runtime: {} };"
            )
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3000)
                for _ in range(3):
                    await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                    await page.wait_for_timeout(1500)
                soup = BeautifulSoup(await page.content(), "html.parser")
                cards = soup.select(
                    "article.placard, [class*='SearchResults'] [class*='Placard'], "
                    "[data-testid*='listing'], .listing-item, [class*='ListingCard']"
                )
                for card in cards[:limit]:
                    title = card.select_one("h2, h3, [class*='title'], [class*='Name']")
                    price = card.select_one("[class*='price'], [class*='Price'], [class*='asking']")
                    cap   = card.select_one("[class*='cap'], [class*='Cap']")
                    sqft  = card.select_one("[class*='sqft'], [class*='size'], [class*='Size']")
                    addr  = card.select_one("[class*='address'], address")
                    link  = card.select_one("a[href]")
                    lst = CREListing(
                        source="LoopNet",
                        title=title.get_text(strip=True) if title else "Unknown",
                        market=market, asset_type=asset_type,
                        address=addr.get_text(strip=True) if addr else "",
                        asking_price=_parse_price(price.get_text(strip=True)) if price else 0.0,
                        cap_rate=_parse_cap_rate(cap.get_text(strip=True)) if cap else 0.0,
                        sqft=_parse_price(sqft.get_text(strip=True)) if sqft else 0.0,
                        url="https://www.loopnet.com" + link["href"] if link and link.get("href","").startswith("/") else (link["href"] if link else ""),
                    )
                    listings.append(lst)
                console.print(f"[blue][LOOPNET][/blue] {len(listings)} listings — {market}")
            except Exception as e:
                console.print(f"[red][LOOPNET ERROR][/red] {e}")
            finally:
                await browser.close()
        return listings


# ─── SOURCE 3: COSTAR (AUTHENTICATED SESSION) ─────────────────
class CoStarAgent:
    """CoStar requires authenticated session — uses httpx with session cookies."""
    SESSION_URL = "https://www.costar.com/api/signin"
    SEARCH_URL  = "https://www.costar.com/api/search/listings"

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=5, max=20))
    async def fetch_listings(self, market: str, asset_type: str = "office", limit: int = 20) -> List[CREListing]:
        if not COSTAR_USER or not COSTAR_PASS:
            console.print("[yellow][COSTAR][/yellow] No credentials — skipping")
            return []
        listings = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            try:
                # Authenticate
                auth_resp = await client.post(self.SESSION_URL, json={
                    "username": COSTAR_USER, "password": COSTAR_PASS
                }, headers={"User-Agent": USER_AGENTS[0]})
                auth_resp.raise_for_status()

                # Search
                search_resp = await client.get(self.SEARCH_URL, params={
                    "market": market, "propertyType": asset_type,
                    "transactionType": "sale", "limit": limit
                })
                search_resp.raise_for_status()
                for item in search_resp.json().get("results", []):
                    lst = CREListing(
                        source="CoStar",
                        title=item.get("propertyName", ""),
                        asset_type=asset_type, market=market,
                        address=item.get("address", ""),
                        asking_price=float(item.get("price", 0)),
                        cap_rate=float(item.get("capRate", 0)),
                        sqft=float(item.get("buildingSize", 0)),
                        year_built=int(item.get("yearBuilt", 0) or 0),
                        url=item.get("listingUrl", ""),
                        raw=item,
                    )
                    listings.append(lst)
                console.print(f"[magenta][COSTAR][/magenta] {len(listings)} listings — {market}")
            except Exception as e:
                console.print(f"[red][COSTAR ERROR][/red] {e}")
        return listings


# ─── PIPELINE ────────────────────────────────────────────────
class SquireScraper:
    def __init__(self):
        self.crexi    = CrexiAgent()
        self.loopnet  = LoopNetAgent()
        self.costar   = CoStarAgent()

    async def scan_market(self, market: str, asset_type: str = "office") -> List[CREListing]:
        console.print(f"\n[bold gold1]⚡ SQUIRE — Scanning {market} | {asset_type}[/bold gold1]")
        # Run all three sources in parallel
        results = await asyncio.gather(
            self.crexi.fetch_listings(market, asset_type),
            self.loopnet.fetch_listings(market, asset_type),
            self.costar.fetch_listings(market, asset_type),
            return_exceptions=True
        )
        all_listings: List[CREListing] = []
        for r in results:
            if isinstance(r, list):
                all_listings.extend(r)

        # Deduplicate by address
        seen, deduped = set(), []
        for lst in all_listings:
            key = lst.address.lower().strip() or lst.title.lower().strip()
            if key and key not in seen:
                seen.add(key)
                deduped.append(lst)

        # Qualify
        for lst in deduped:
            lst.qualify()

        qualified = [l for l in deduped if l.qualified]
        console.print(f"[bold green]  → {len(deduped)} total | {len(qualified)} qualified (Batman gate)[/bold green]")
        return deduped

    async def scan_all(self, asset_type: str = "office") -> List[CREListing]:
        all_results = []
        for market in TARGET_MARKETS:
            results = await self.scan_market(market, asset_type)
            all_results.extend(results)
        return all_results

    def print_table(self, listings: List[CREListing]):
        qualified = [l for l in listings if l.qualified]
        t = Table(title=f"🦇 Squire Results — {len(qualified)}/{len(listings)} Qualified", style="bold")
        t.add_column("Source", style="cyan", width=12)
        t.add_column("Market", width=14)
        t.add_column("Title", width=30)
        t.add_column("Price", style="green", justify="right", width=12)
        t.add_column("Cap Rate", style="yellow", justify="right", width=9)
        t.add_column("Score", style="magenta", justify="right", width=7)
        t.add_column("✓", justify="center", width=3)
        for l in sorted(listings, key=lambda x: x.qualify_score, reverse=True):
            t.add_row(
                l.source[:12],
                l.market[:14],
                l.title[:30],
                f"${l.asking_price:,.0f}" if l.asking_price else "N/A",
                f"{l.cap_rate*100:.1f}%" if l.cap_rate else "N/A",
                str(l.qualify_score),
                "✓" if l.qualified else "",
            )
        console.print(t)


# ─── CLI ─────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(description="Squire — Live CRE Intelligence Agent")
    parser.add_argument("--market", type=str, help="Single market to scan (e.g. 'Miami')")
    parser.add_argument("--asset-type", type=str, default="office", help="Asset type (office, retail, industrial, multifamily)")
    parser.add_argument("--all", action="store_true", help="Scan all TARGET_MARKETS from .env")
    parser.add_argument("--output", type=str, help="Save results to JSON file")
    args = parser.parse_args()

    scraper = SquireScraper()
    if args.all:
        results = await scraper.scan_all(args.asset_type)
    elif args.market:
        results = await scraper.scan_market(args.market, args.asset_type)
    else:
        console.print("[red]Provide --market or --all[/red]")
        return

    scraper.print_table(results)

    if args.output:
        with open(args.output, "w") as f:
            json.dump([l.to_dict() for l in results], f, indent=2, default=str)
        console.print(f"\n[green]✓ Saved {len(results)} listings → {args.output}[/green]")

if __name__ == "__main__":
    asyncio.run(main())
