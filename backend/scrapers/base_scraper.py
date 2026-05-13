import logging
import random
import time
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


class BaseScraper:
    """Selenium base wrapper with stealth options and human-like delays."""

    def __init__(self, headless: bool = True, slow_mode: bool = False) -> None:
        self.headless = headless
        self.slow_mode = slow_mode
        self.driver: Optional[webdriver.Chrome] = None

    def _build_options(self) -> ChromeOptions:
        options = ChromeOptions()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-gpu")
        options.add_argument("--lang=en-US")
        options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")

        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_experimental_option(
            "prefs",
            {"profile.managed_default_content_settings.images": 2},
        )
        return options

    def start(self) -> webdriver.Chrome:
        if self.driver is not None:
            return self.driver
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=self._build_options())
        self.driver.execute_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        logger.info("Chrome driver started (headless=%s)", self.headless)
        return self.driver

    def stop(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:  # noqa: BLE001
                logger.exception("Error while quitting driver")
            finally:
                self.driver = None

    def __enter__(self) -> "BaseScraper":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def get(self, url: str, wait_seconds: float = 1.5) -> None:
        if self.driver is None:
            self.start()
        assert self.driver is not None
        logger.debug("GET %s", url)
        self.driver.get(url)
        self._human_delay(base=wait_seconds)

    def _human_delay(self, base: float = 1.0) -> None:
        delay = base + random.uniform(0.3, 1.2)
        if self.slow_mode:
            delay += random.uniform(0.5, 1.5)
        time.sleep(delay)

    def wait_for(self, by: str, selector: str, timeout: int = 15) -> WebElement:
        assert self.driver is not None, "Driver not started"
        return WebDriverWait(self.driver, timeout).until(
            EC.visibility_of_element_located((by, selector))
        )

    def safe_find(self, by: str, selector: str) -> Optional[WebElement]:
        if self.driver is None:
            return None
        try:
            return self.driver.find_element(by, selector)
        except Exception:  # noqa: BLE001
            return None

    def safe_find_all(self, by: str, selector: str) -> list[WebElement]:
        if self.driver is None:
            return []
        try:
            return self.driver.find_elements(by, selector)
        except Exception:  # noqa: BLE001
            return []

    def scroll_to_bottom(self) -> None:
        if self.driver is None:
            return
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        self._human_delay(base=0.8)


__all__ = ["BaseScraper", "By"]
