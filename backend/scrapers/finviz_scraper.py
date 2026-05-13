import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By

from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


@dataclass
class TickerSummary:
    ticker: str
    company: str = ""
    sector: str = ""
    industry: str = ""
    market_cap: str = ""
    pe: str = ""
    price: str = ""
    change_pct: str = ""
    volume: str = ""


@dataclass
class TickerDetail:
    ticker: str
    price: Optional[float] = None
    change_pct: Optional[float] = None
    market_cap: str = ""
    pe: str = ""
    eps: str = ""
    forward_pe: str = ""
    iv_pct: str = ""
    short_float: str = ""
    avg_volume: str = ""
    rel_volume: str = ""
    earnings_date: str = ""
    target_price: str = ""
    recommendation: str = ""
    rsi: str = ""
    sma_20: str = ""
    sma_50: str = ""
    sma_200: str = ""
    high_52w: str = ""
    low_52w: str = ""
    news: list[dict] = field(default_factory=list)


SCREENER_COLUMNS = [
    "no", "ticker", "company", "sector", "industry", "country",
    "market_cap", "pe", "price", "change_pct", "volume",
]


SNAPSHOT_FIELD_MAP = {
    "Price": "price",
    "Change": "change_pct",
    "Market Cap": "market_cap",
    "P/E": "pe",
    "EPS (ttm)": "eps",
    "Forward P/E": "forward_pe",
    "Volatility": "iv_pct",
    "Short Float": "short_float",
    "Avg Volume": "avg_volume",
    "Rel Volume": "rel_volume",
    "Earnings": "earnings_date",
    "Target Price": "target_price",
    "Recom": "recommendation",
    "RSI (14)": "rsi",
    "SMA20": "sma_20",
    "SMA50": "sma_50",
    "SMA200": "sma_200",
    "52W High": "high_52w",
    "52W Low": "low_52w",
}


def _parse_float(text: str) -> Optional[float]:
    if not text:
        return None
    clean = text.replace(",", "").replace("%", "").strip()
    try:
        return float(clean)
    except ValueError:
        return None


class FinVizScraper(BaseScraper):
    SCREENER_URL = (
        "https://finviz.com/screener.ashx"
        "?v=111&f=cap_midover,sh_avgvol_o500,sh_opt_option,ta_highlow52w_a70h&ft=4&o=-volume"
    )
    QUOTE_URL = "https://finviz.com/quote.ashx?t={ticker}"
    SP500_GAINERS = "https://finviz.com/screener.ashx?v=111&f=idx_sp500&o=-change"
    SP500_LOSERS = "https://finviz.com/screener.ashx?v=111&f=idx_sp500&o=change"

    # ───────────────────────── screener ─────────────────────────

    def scrape_screener(self, max_pages: int = 5) -> list[TickerSummary]:
        return self._scrape_screener_url(self.SCREENER_URL, max_pages=max_pages)

    def _scrape_screener_url(self, base_url: str, max_pages: int = 5) -> list[TickerSummary]:
        results: list[TickerSummary] = []
        self.get(base_url, wait_seconds=2.0)

        total_results = self._extract_total_results()
        pages_needed = 1
        if total_results > 0:
            pages_needed = min(max_pages, math.ceil(total_results / 20))

        for page in range(1, pages_needed + 1):
            if page > 1:
                offset = (page - 1) * 20 + 1
                sep = "&" if "?" in base_url else "?"
                url = f"{base_url}{sep}r={offset}"
                self.get(url, wait_seconds=2.0)
                time.sleep(2)

            page_rows = self._parse_screener_table()
            results.extend(page_rows)
            logger.info("דף %d מתוך %d – נמצאו %d מניות", page, pages_needed, len(page_rows))

        return results

    def _extract_total_results(self) -> int:
        el = self.safe_find(By.ID, "screener-total")
        if el is None:
            el = self.safe_find(By.CSS_SELECTOR, ".screener_total")
        if el is None:
            return 0
        text = (el.text or "").strip()
        for token in text.replace("#", " ").replace("/", " ").split():
            try:
                return int(token.replace(",", ""))
            except ValueError:
                continue
        return 0

    def _parse_screener_table(self) -> list[TickerSummary]:
        if self.driver is None:
            return []
        html = self.driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", id="screener-table")
        if table is None:
            table = soup.find("table", class_="screener_table")
        if table is None:
            logger.warning("Screener table not found")
            return []

        rows = table.find_all("tr")
        results: list[TickerSummary] = []
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 11:
                continue
            values = [c.get_text(strip=True) for c in cells]
            row_dict = {SCREENER_COLUMNS[i]: values[i] for i in range(min(len(values), len(SCREENER_COLUMNS)))}
            if not row_dict.get("ticker") or not row_dict["ticker"].isalpha():
                continue
            results.append(
                TickerSummary(
                    ticker=row_dict.get("ticker", ""),
                    company=row_dict.get("company", ""),
                    sector=row_dict.get("sector", ""),
                    industry=row_dict.get("industry", ""),
                    market_cap=row_dict.get("market_cap", ""),
                    pe=row_dict.get("pe", ""),
                    price=row_dict.get("price", ""),
                    change_pct=row_dict.get("change_pct", ""),
                    volume=row_dict.get("volume", ""),
                )
            )
        return results

    # ───────────────────────── single ticker ─────────────────────────

    def scrape_ticker(self, ticker: str) -> TickerDetail:
        ticker = ticker.upper().strip()
        detail = TickerDetail(ticker=ticker)

        self.get(self.QUOTE_URL.format(ticker=ticker), wait_seconds=1.8)
        if self.driver is None:
            return detail

        html = self.driver.page_source
        if "not found" in html.lower() and "quote" in html.lower():
            logger.warning("Ticker %s not found on FinViz", ticker)
            return detail

        soup = BeautifulSoup(html, "html.parser")

        snapshot = soup.find("table", class_="snapshot-table2")
        if snapshot is None:
            logger.warning("Snapshot table not found for %s", ticker)
        else:
            cells = snapshot.find_all("td")
            for i in range(0, len(cells) - 1, 2):
                label = cells[i].get_text(strip=True)
                value = cells[i + 1].get_text(strip=True)
                attr = SNAPSHOT_FIELD_MAP.get(label)
                if attr is None:
                    continue
                if attr == "price":
                    detail.price = _parse_float(value)
                elif attr == "change_pct":
                    detail.change_pct = _parse_float(value)
                else:
                    setattr(detail, attr, value)

        detail.news = self._parse_news(soup, limit=10)
        return detail

    def _parse_news(self, soup: BeautifulSoup, limit: int = 10) -> list[dict]:
        news_table = soup.find("table", id="news-table")
        if news_table is None:
            return []

        items: list[dict] = []
        last_date = ""
        for row in news_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            time_text = cells[0].get_text(strip=True)
            if " " in time_text:
                last_date, _, time_part = time_text.partition(" ")
                time_value = f"{last_date} {time_part}"
            else:
                time_value = f"{last_date} {time_text}" if last_date else time_text

            link = cells[1].find("a")
            source_span = cells[1].find("span") or cells[1].find("div", class_="news-link-right")
            items.append(
                {
                    "time": time_value.strip(),
                    "title": link.get_text(strip=True) if link else cells[1].get_text(strip=True),
                    "url": link.get("href", "") if link else "",
                    "source": source_span.get_text(strip=True) if source_span else "",
                }
            )
            if len(items) >= limit:
                break
        return items

    # ───────────────────────── S&P 500 movers ─────────────────────────

    def scrape_sp500_movers(self) -> dict:
        gainers = self._scrape_screener_url(self.SP500_GAINERS, max_pages=1)
        losers = self._scrape_screener_url(self.SP500_LOSERS, max_pages=1)
        return {"gainers": gainers, "losers": losers}


__all__ = ["FinVizScraper", "TickerSummary", "TickerDetail"]
