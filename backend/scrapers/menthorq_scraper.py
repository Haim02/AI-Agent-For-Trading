import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By

from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


@dataclass
class GEXData:
    ticker: str = "SPX"
    timestamp: datetime = field(default_factory=datetime.utcnow)
    spot_price: float = 0.0
    gex_total: float = 0.0
    dex_total: float = 0.0
    gamma_flip_level: float = 0.0
    call_wall: float = 0.0
    put_wall: float = 0.0
    top_gex_strikes: list[dict] = field(default_factory=list)
    regime: str = "positive"
    dealer_behavior: str = ""
    note: Optional[str] = None


def _parse_number(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text)
    try:
        return float(cleaned)
    except ValueError:
        return None


class MenthorQScraper(BaseScraper):
    LOGIN_URL = "https://menthorq.com/wp-login.php"
    DASHBOARD_URL = "https://menthorq.com/dashboard"

    def login(self) -> bool:
        username = os.getenv("MENTHORQ_USERNAME")
        password = os.getenv("MENTHORQ_PASSWORD")
        if not username or not password:
            logger.error("חסרים פרטי התחברות ל-MenthorQ (MENTHORQ_USERNAME/MENTHORQ_PASSWORD)")
            return False

        try:
            self.get(self.LOGIN_URL, wait_seconds=1.5)
            user_field = self.wait_for(By.ID, "user_login", timeout=15)
            user_field.clear()
            user_field.send_keys(username)

            pass_field = self.safe_find(By.ID, "user_pass")
            if pass_field is None:
                logger.error("שדה הסיסמה לא נמצא בעמוד")
                return False
            pass_field.clear()
            pass_field.send_keys(password)

            submit = self.safe_find(By.ID, "wp-submit")
            if submit is None:
                logger.error("כפתור ההתחברות לא נמצא")
                return False
            submit.click()
            self._human_delay(base=3.0)

            assert self.driver is not None
            current = self.driver.current_url or ""
            if "wp-login" in current and "loggedout" not in current:
                logger.error("ההתחברות נכשלה – נשארנו בעמוד הלוגין")
                return False
            logger.info("התחברות ל-MenthorQ הצליחה")
            return True
        except Exception:  # noqa: BLE001
            logger.exception("שגיאה בלתי צפויה בעת ההתחברות ל-MenthorQ")
            return False

    def scrape_gex_data(self) -> GEXData:
        if not self.login():
            return self._mock_gex("ההתחברות ל-MenthorQ נכשלה")

        try:
            self.get(self.DASHBOARD_URL, wait_seconds=2.5)
            try:
                self.wait_for(By.CSS_SELECTOR, "[data-gex], .gex-chart, table.gex-table", timeout=20)
            except Exception:  # noqa: BLE001
                logger.warning("לא נטענה תצוגת ה-GEX בזמן הצפוי")

            assert self.driver is not None
            html = self.driver.page_source
            soup = BeautifulSoup(html, "html.parser")

            spot_price = self._extract_metric(soup, ["spot", "spx-spot", "spot-price"]) or 0.0
            gex_total = self._extract_metric(soup, ["gex-total", "total-gex"]) or 0.0
            dex_total = self._extract_metric(soup, ["dex-total", "total-dex"]) or 0.0
            gamma_flip = self._extract_metric(soup, ["gamma-flip", "flip-level"]) or 0.0
            call_wall = self._extract_metric(soup, ["call-wall"]) or 0.0
            put_wall = self._extract_metric(soup, ["put-wall"]) or 0.0
            top_strikes = self._extract_top_strikes(soup)

            regime = "positive" if gex_total > 0 else "negative"
            dealer_behavior = (
                "קונים בירידות, מוכרים בעליות – שוק רגוע"
                if regime == "positive"
                else "מוכרים בירידות, קונים בעליות – שוק תנודתי"
            )

            return GEXData(
                ticker="SPX",
                timestamp=datetime.utcnow(),
                spot_price=spot_price,
                gex_total=gex_total,
                dex_total=dex_total,
                gamma_flip_level=gamma_flip,
                call_wall=call_wall,
                put_wall=put_wall,
                top_gex_strikes=top_strikes,
                regime=regime,
                dealer_behavior=dealer_behavior,
            )
        except Exception:  # noqa: BLE001
            logger.exception("שגיאה בעת קריאת נתוני GEX מ-MenthorQ")
            return self._mock_gex("נדרשת בדיקה ידנית")

    def _extract_metric(self, soup: BeautifulSoup, keys: list[str]) -> Optional[float]:
        for key in keys:
            el = soup.find(attrs={"data-metric": key})
            if el is None:
                el = soup.find(id=key) or soup.find(class_=key)
            if el is not None:
                value = _parse_number(el.get_text(strip=True))
                if value is not None:
                    return value
        return None

    def _extract_top_strikes(self, soup: BeautifulSoup, limit: int = 5) -> list[dict]:
        table = soup.find("table", class_="gex-strikes") or soup.find("table", id="gex-strikes")
        if table is None:
            return []
        rows = table.find_all("tr")
        results: list[dict] = []
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            strike = _parse_number(cells[0].get_text(strip=True))
            gex_value = _parse_number(cells[1].get_text(strip=True))
            type_text = cells[2].get_text(strip=True).lower()
            if strike is None or gex_value is None:
                continue
            results.append(
                {
                    "strike": strike,
                    "gex_value": gex_value,
                    "type": "call" if "call" in type_text else "put",
                }
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _mock_gex(note: str) -> GEXData:
        return GEXData(
            ticker="SPX",
            timestamp=datetime.utcnow(),
            spot_price=0.0,
            gex_total=0.0,
            dex_total=0.0,
            gamma_flip_level=0.0,
            call_wall=0.0,
            put_wall=0.0,
            top_gex_strikes=[],
            regime="positive",
            dealer_behavior="נדרשת בדיקה ידנית",
            note=note,
        )


__all__ = ["MenthorQScraper", "GEXData"]
