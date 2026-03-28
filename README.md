# Squire — Live CRE Intelligence Agent

> *"Batman doesn't wait for the deal to come to him."*

Built by **Brandon Hines [Ψ̂-001]** · CortexChain, Inc. × Waveform Tech LLC

---

## What It Is

Squire is a multi-source commercial real estate data pipeline that:

1. **Scrapes live listings** from CoStar, LoopNet, and Crexi
2. **Qualifies assets** through cap rate + DSCR gates
3. **Injects trophy-grade targets** into the AgentSeeker / Forge system

One command. Three sources. Only the deals that pass the Batman gate come out the other side.

---

## Architecture

```
SquireScraper
    ├── Source 1: Crexi (JSON API + HTML fallback via ScraperAPI)
    ├── Source 2: LoopNet (Playwright headless browser)
    ├── Source 3: CoStar (authenticated session)
    ├── DataNormalizer → unified CREListing schema
    ├── QualificationFilter → cap rate ≥ 5%, DSCR ≥ 1.25
    └── Output → JSON export / AgentSeeker inject
```

---

## Install

```bash
pip install playwright httpx beautifulsoup4 scraperapi-sdk python-dotenv rich tenacity
playwright install chromium
```

---

## Environment Variables

```env
SCRAPERAPI_KEY=your_scraperapi_key
COSTAR_USERNAME=your_costar_email
COSTAR_PASSWORD=your_costar_password
CREXI_API_KEY=your_crexi_api_key
MIN_CAP_RATE=0.05
MIN_DSCR=1.25
TARGET_MARKETS=New York City,Los Angeles,Miami,Dallas,Chicago
```

---

## Run

```bash
# Single market scan
python squire_scraper.py --market "Miami" --asset-type office

# Full pipeline (all TARGET_MARKETS)
python squire_scraper.py --all

# Output to JSON
python squire_scraper.py --all --output results.json
```

---

## Output Schema

```json
{
  "listing_id": "LST-A1B2C3D4",
  "source": "Crexi",
  "title": "Class A Office Tower",
  "asset_type": "office",
  "market": "Miami",
  "address": "1000 Brickell Ave, Miami, FL 33131",
  "asking_price": 12500000,
  "noi": 750000,
  "cap_rate": 0.06,
  "sqft": 45000,
  "year_built": 2018,
  "url": "https://www.crexi.com/properties/...",
  "qualified": true,
  "qualify_score": 8.31,
  "scraped_at": "2026-03-28T01:30:00"
}
```

---

## Qualification Gate (Batman Filter)

- `cap_rate >= MIN_CAP_RATE` (default 5%)
- `estimated_dscr >= MIN_DSCR` (default 1.25x, derived from cap rate / 6.5% debt constant)
- `asking_price > 0`

Only assets passing all three gates are marked `qualified: true`.

---

## Part of the Batman Robb Report Stack

```
Squire (this repo)     → live CRE data pipeline
AgentSeeker            → founder/investor matching
Forge                  → deal structuring engine
Batman Robb Report     → sovereign command surface
```

---

*Built with Perplexity Computer · Powered by NVIDIA NIM*
