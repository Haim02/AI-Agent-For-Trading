"""Loads GEX / Flow / Options knowledge into ChromaDB for RAG retrieval."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from memory.long_term import LongTermMemory

logger = logging.getLogger(__name__)

GEX_KNOWLEDGE_BASE: list[dict[str, Any]] = [
    {
        "id": "gex_basics_001",
        "topic": "GEX - מה זה ואיך עובד",
        "category": "gex_fundamentals",
        "content": (
            "Gamma Exposure (GEX) הוא מדד אגרגטיבי של כמה Gamma\n"
            "יש ל-Market Makers בכל Strike.\n"
            "נוסחה: GEX(strike) = Gamma × OI × 100 × Spot²\n"
            "Calls תורמים GEX חיובי.\n"
            "Puts תורמים GEX שלילי.\n"
            "ככל שיש יותר Open Interest ב-Strike מסוים,\n"
            "כך ה-Market Makers צריכים לגדר יותר באותו Strike.\n"
            "זה יוצר Support/Resistance מבני - לא טכני."
        ),
    },
    {
        "id": "gex_regime_positive_002",
        "topic": "Positive Gamma Regime",
        "category": "gex_regime",
        "content": (
            "כש-Total Net GEX חיובי, השוק נמצא ב-Positive Gamma.\n"
            "Market Makers Long Gamma:\n"
            "- קונים כשמחיר יורד (מדכא ירידות)\n"
            "- מוכרים כשמחיר עולה (מדכא עליות)\n"
            "אפקט: שוק יציב, תנודתיות נמוכה, Pinning.\n"
            "אסטרטגיות מועדפות:\n"
            "- Iron Condor\n"
            "- Short Strangle\n"
            "- Credit Spreads (Bull Put / Bear Call)\n"
            "- Calendar Spread\n"
            "Strikes: מחוץ ל-Walls (Call Wall ו-Put Wall)."
        ),
    },
    {
        "id": "gex_regime_negative_003",
        "topic": "Negative Gamma Regime",
        "category": "gex_regime",
        "content": (
            "כש-Total Net GEX שלילי, השוק ב-Negative Gamma.\n"
            "Market Makers Short Gamma:\n"
            "- מוכרים כשמחיר יורד (מגביר ירידות)\n"
            "- קונים כשמחיר עולה (מגביר עליות)\n"
            "אפקט: שוק תנודתי, Breakouts, Gamma Squeezes.\n"
            "אסטרטגיות מועדפות:\n"
            "- Long Straddle / Strangle\n"
            "- Debit Spreads (Call/Put)\n"
            "- Long Options\n"
            "- Momentum strategies\n"
            "הימנע ממכירת פרמיה עירומה ב-Negative Gamma!"
        ),
    },
    {
        "id": "call_wall_004",
        "topic": "Call Wall - תקרה מבנית",
        "category": "gex_levels",
        "content": (
            "Call Wall = ה-Strike עם ה-GEX החיובי הגבוה ביותר.\n"
            "מכניזם: סוחרים קונים Calls → Dealers שורטים Calls\n"
            "→ Dealers קונים מניות כ-Hedge.\n"
            "ככל שמחיר מתקרב ל-Call Wall → Dealers מוכרים יותר.\n"
            "Call Wall פועל כתקרה (Resistance).\n"
            "מה קורה כש-Call Wall נשבר (סגירה מעליו):\n"
            "1. ה-Selling Pressure נעלם\n"
            "2. Dealers קונים כ-Hedge (Gamma Squeeze)\n"
            "3. תנועה אקספלוסיבית כלפי מעלה\n"
            "חוק: שבירה = סגירה מעל, לא נקודה תוך-יומית.\n"
            "שימוש מעשי: מקם Short Strike של Credit Spread\n"
            "מעט מעל ה-Call Wall."
        ),
    },
    {
        "id": "put_wall_005",
        "topic": "Put Wall - רצפה מבנית",
        "category": "gex_levels",
        "content": (
            "Put Wall = ה-Strike עם ה-GEX השלילי הגבוה ביותר.\n"
            "מכניזם: סוחרים קונים Puts → Dealers שורטים Puts\n"
            "→ Dealers מוכרים מניות כ-Hedge.\n"
            "ככל שמחיר יורד ל-Put Wall → Dealers קונים יותר.\n"
            "Put Wall פועל כרצפה (Support).\n"
            "מה קורה כש-Put Wall נשבר (סגירה מתחתיו):\n"
            "1. ה-Buying Pressure נעלם\n"
            "2. Gamma Cascade - ירידה מואצת\n"
            "3. תנועה אקספלוסיבית כלפי מטה\n"
            "שימוש מעשי: בירידות - לקנות ה-DIP ליד Put Wall.\n"
            "שמור Short Strike של Bull Put Spread מתחת ל-Put Wall."
        ),
    },
    {
        "id": "gamma_flip_006",
        "topic": "Gamma Flip - נקודת המפנה",
        "category": "gex_levels",
        "content": (
            "Gamma Flip = המחיר שבו Net GEX עובר מחיובי לשלילי.\n"
            "זוהי נקודת המפנה הקריטית ביותר.\n"
            "מעל Gamma Flip = Positive Gamma = שוק יציב.\n"
            "מתחת Gamma Flip = Negative Gamma = שוק תנודתי.\n"
            "חציה כלפי מטה = אזהרה! מכר Premium מיד.\n"
            "חציה כלפי מעלה = חזרה ליציבות, תנאים טובים.\n"
            "ב-0DTE: הגמא פליפ זזה לאורך היום עם הזמן.\n"
            "בדוק בכל בוקר איפה ה-Gamma Flip ביחס לספוט.\n"
            "אם ספוט < Gamma Flip = הגבר דריכות."
        ),
    },
    {
        "id": "gex_profiles_007",
        "topic": "4 פרופילי GEX לפי Option Alpha",
        "category": "gex_profiles",
        "content": (
            "פרופיל 1 - WALL (קיר):\n"
            "Strike יחיד עם GEX מסיבי. Support/Resistance חזק.\n"
            "אסטרטגיה: Credit Spreads סביב ה-Wall.\n"
            "\n"
            "פרופיל 2 - PILLARS (עמודים):\n"
            "2-3 Strikes משמעותיים. Trading Range.\n"
            "אסטרטגיה: Iron Condor בין העמודים.\n"
            "\n"
            "פרופיל 3 - SLIDE (מדרון):\n"
            "GEX יורד הדרגתית. מגמה כיוונית.\n"
            "אסטרטגיה: Directional Calls/Puts.\n"
            "\n"
            "פרופיל 4 - PIN (סיכה):\n"
            "GEX מרוכז ב-ATM. Max Pain / Pinning.\n"
            "אסטרטגיה: Iron Butterfly / Calendar Spread."
        ),
    },
    {
        "id": "0dte_gex_008",
        "topic": "0DTE ו-GEX - כללי מסחר",
        "category": "0dte_strategy",
        "content": (
            "0DTE = אופציות שפוקעות באותו יום.\n"
            "59% מנפח SPX הוא 0DTE (CBOE 2024).\n"
            "Gamma ב-0DTE = קיצוני (Theta = 0, Gamma → ∞).\n"
            "תנועה קטנה = שינוי Delta ענקי = Hedging מיידי.\n"
            "\n"
            "חלונות זמן מועדפים:\n"
            "- 9:45-10:30 EST (16:45-17:30 ישראל קיץ):\n"
            "  אחרי גילוי כיוון הפתיחה\n"
            "- 14:00-15:00 EST (21:00-22:00 ישראל קיץ):\n"
            "  Theta Crush לפני סגירה\n"
            "\n"
            "כללי 0DTE:\n"
            "1. בדוק GEX Regime לפני כל כניסה\n"
            "2. Positive Gamma → Sell Premium (Iron Condor/Butterfly)\n"
            "3. Negative Gamma → Buy Premium (Debit Spread)\n"
            "4. Delta מטרה: 0.05-0.10\n"
            "5. Strikes מחוץ ל-Walls (לא לחצות אותם!)\n"
            "6. תמיד Stop Loss = 2x ה-Credit\n"
            "7. יצא ב-50% Profit"
        ),
    },
    {
        "id": "options_flow_009",
        "topic": "Options Flow - קריאת Flow",
        "category": "options_flow",
        "content": (
            "Options Flow = מעקב עסקאות אופציות בזמן אמת.\n"
            "\n"
            "סוגי עסקאות:\n"
            "SWEEP: הזמנה גדולה על מספר בורסות בו-זמנית.\n"
            "→ דחיפות גבוהה. הסוחר משלם מחיר Ask.\n"
            "→ סיגנל חזק לכוונה מיידית.\n"
            "\n"
            "BLOCK: עסקה גדולה אחת, לרוב OTC.\n"
            "→ Smart Money / מוסדיים.\n"
            "→ יכול להיות Hedge - בדוק הקשר.\n"
            "\n"
            "SPLIT: כמו Sweep אבל בבורסה אחת.\n"
            "\n"
            "קביעת Sentiment:\n"
            "Call ב-Ask → Bullish (קנייה אגרסיבית)\n"
            "Call ב-Bid → Bearish (מכירה אגרסיבית)\n"
            "Put ב-Ask → Bearish (קנייה אגרסיבית)\n"
            "Put ב-Bid → Bullish (מכירה / סגירת Hedge)\n"
            "\n"
            "Volume/OI Ratio:\n"
            "Vol > OI = פוזיציה חדשה = סיגנל חזק\n"
            "Vol < OI = סגירת פוזיציה = פחות משמעותי\n"
            "Vol/OI > 5 = פעילות חריגה מאוד"
        ),
    },
    {
        "id": "flow_signals_010",
        "topic": "Options Flow - סיגנלים חזקים",
        "category": "options_flow",
        "content": (
            "סיגנלים חזקים ב-Options Flow:\n"
            "\n"
            "STRONG BULLISH:\n"
            "- מספר Call Sweeps + Block גדול על אותו Ticker\n"
            "- Premium > $500k על Strike OTM\n"
            "- Volume >> Open Interest (פוזיציה חדשה ברורה)\n"
            "- Expiry קצר (שבועות, לא חודשים)\n"
            "\n"
            "STRONG BEARISH:\n"
            "- Put Sweeps מואצים אחרי Block גדול\n"
            "- פעילות חריגה לפני earnings/FDA\n"
            "\n"
            "אזהרות - לא תמיד כוונה ישירה:\n"
            "- Block גדול יכול להיות Hedge על מניות\n"
            "- Multi-leg trades = אסטרטגיה מורכבת\n"
            "- תמיד בדוק: האם Vol > OI?\n"
            "\n"
            "שילוב עם GEX:\n"
            "אם Flow Bullish + GEX Positive = סיגנל חזק\n"
            "אם Flow Bullish + GEX Negative = שים לב\n"
            "אם יש סתירה = המתן לבהירות"
        ),
    },
    {
        "id": "strike_selection_011",
        "topic": "בחירת Strikes לפי GEX",
        "category": "strategy",
        "content": (
            "כלל זהב: הכנס Strikes מחוץ ל-Walls, לא לחצות.\n"
            "\n"
            "Credit Spreads / Iron Condor:\n"
            "- Short Call Strike: מעט מעל Call Wall\n"
            "- Short Put Strike: מעט מתחת Put Wall\n"
            "- ה-Walls מגנים על Strikes שלך\n"
            "\n"
            "Bull Put Spread (שורי):\n"
            "- Short Put: מעט מעל Put Wall\n"
            "- Long Put: $5-10 מתחת\n"
            "\n"
            "Bear Call Spread (דובי):\n"
            "- Short Call: מעט מתחת Call Wall\n"
            "- Long Call: $5-10 מעל\n"
            "\n"
            "Delta מטרה:\n"
            "- Short Strike: Delta 0.15-0.20 (Tastytrade ~0.16)\n"
            "- 0DTE מיוחד: Delta 0.05-0.10\n"
            "\n"
            "Expiration:\n"
            "- רגיל: 30-45 DTE (Tastytrade method)\n"
            "- שבועי: 7-14 DTE\n"
            "- 0DTE: אותו יום\n"
            "\n"
            "עקרון: GEX Walls חזקים יותר מ-Technical S/R\n"
            "כי הם מבוססים על Hedging מכאני, לא פסיכולוגי."
        ),
    },
    {
        "id": "daily_checklist_012",
        "topic": "צ'קליסט יומי לפני מסחר",
        "category": "daily_process",
        "content": (
            "צ'קליסט GEX יומי - לפני כל יום מסחר:\n"
            "\n"
            "1. GEX Regime: Positive או Negative?\n"
            "2. Call Wall: איפה? כמה רחוק מהספוט?\n"
            "3. Put Wall: איפה? כמה רחוק מהספוט?\n"
            "4. Gamma Flip: מעל/מתחת לספוט?\n"
            "5. GEX Profile: Wall/Pillar/Slide/Pin?\n"
            "6. Options Flow: Bullish/Bearish/Neutral?\n"
            "7. VIX: מתחת 20 (מכור) / מעל 20 (זהירות)?\n"
            "8. אירועי מאקרו היום? (FOMC/CPI/Earnings?)\n"
            "9. IV Rank: מעל 50 (מכור) / מתחת 25 (קנה)?\n"
            "\n"
            "הגיון אסטרטגי:\n"
            "Positive GEX + VIX <20 + IV >50 = \n"
            "    → מצוין לגישה Tastytrade\n"
            "    → Iron Condor / Strangle עם Strikes מחוץ ל-Walls\n"
            "\n"
            "Negative GEX + VIX >25 = \n"
            "    → אל תמכור פרמיה!\n"
            "    → שקול Debit Spreads בכיוון ה-Flow"
        ),
    },
    {
        "id": "haim_preferences_013",
        "topic": "העדפות חיים - הגדרות אישיות",
        "category": "user_profile",
        "content": (
            "משתמש: חיים, סוחר אופציות בישראל.\n"
            "ברוקר: Interactive Brokers Israel (ללא API ישיר).\n"
            "יעד: $1,000 רווח שבועי.\n"
            "מתודולוגיה: Tastytrade.\n"
            "\n"
            "כללי Tastytrade של חיים:\n"
            "- כניסה: 30-45 DTE\n"
            "- יציאה: 50% Profit Target\n"
            "- Stop Loss: 2x ה-Credit שנגבה\n"
            "- Strike: Delta ~0.16 (מחוץ ל-Expected Move)\n"
            "- IV Rank > 50: זהב למכירת פרמיה\n"
            "\n"
            "שעות מסחר בישראל (קיץ):\n"
            "- פתיחת שוק: 16:30\n"
            "- סגירת שוק: 23:00\n"
            "- פרה-מרקט: מ-14:30\n"
            "\n"
            "כשחיים שואל על \"עכשיו\":\n"
            "- הוא מתכוון לשעה בישראל\n"
            "- תמיד הצג שעות בשעון ישראל\n"
            "- פורמט תאריך: DD/MM/YYYY\n"
            "\n"
            "אסטרטגיות מועדפות (לפי סדר):\n"
            "1. Iron Condor (VIX נמוך, Positive GEX)\n"
            "2. Bull Put Spread (מגמה עולה)\n"
            "3. Short Strangle (IV גבוה מאוד)\n"
            "4. 0DTE SPX (עדיין לומד)"
        ),
    },
    {
        "id": "gex_flow_combined_014",
        "topic": "שילוב GEX + Flow - מטריצת החלטות",
        "category": "strategy",
        "content": (
            "מטריצת החלטות: GEX Regime × Options Flow\n"
            "\n"
            "[Positive GEX] × [Flow Bullish]:\n"
            "→ סיגנל שורי חזק\n"
            "→ Bull Put Spread / Iron Condor עם הטיה שורית\n"
            "→ Short Put Strike ליד Put Wall\n"
            "\n"
            "[Positive GEX] × [Flow Bearish]:\n"
            "→ סתירה! המתן לבהירות\n"
            "→ אולי הגנה מוסדית (Hedge)\n"
            "→ הקטן גודל פוזיציה\n"
            "\n"
            "[Positive GEX] × [Flow Neutral]:\n"
            "→ שוק יציב ללא כיוון\n"
            "→ Iron Condor סימטרי\n"
            "→ Strikes מחוץ ל-Walls\n"
            "\n"
            "[Negative GEX] × [Flow Bullish]:\n"
            "→ Potential Squeeze\n"
            "→ שקול Long Calls קצרי DTE\n"
            "→ נהל סיכון בקפידה\n"
            "\n"
            "[Negative GEX] × [Flow Bearish]:\n"
            "→ סיגנל דובי חזק\n"
            "→ Long Puts / Bear Spread\n"
            "→ אל תמכור פרמיה!\n"
            "\n"
            "[Negative GEX] × [Flow Neutral]:\n"
            "→ תנודתיות גבוהה ללא כיוון\n"
            "→ Long Straddle / Strangle\n"
            "→ המתן לאות ברור"
        ),
    },
    {
        "id": "unusual_whales_015",
        "topic": "Unusual Whales - כלים ופיצ'רים",
        "category": "tools",
        "content": (
            "Unusual Whales מספק:\n"
            "\n"
            "1. Options Flow Feed:\n"
            "   - כל עסקת אופציות בזמן אמת\n"
            "   - סינון לפי: Premium, Type, Expiry, Sector\n"
            "   - סיווג: Sweep/Block/Split\n"
            "   - Sentiment אוטומטי: Bullish/Bearish\n"
            "\n"
            "2. GEX Tools:\n"
            "   - GEX לפי Strike\n"
            "   - GEX לפי Expiry\n"
            "   - Net GEX (Calls פחות Puts)\n"
            "   - Spot GEX exposures כל דקה\n"
            "\n"
            "3. 0DTE Feature:\n"
            "   - מעקב Flow 0DTE ל-SPY ו-QQQ\n"
            "   - שינויי GEX תוך-יומיים\n"
            "   - Market Tide: Net Flow לפי שעה\n"
            "\n"
            "4. Screener:\n"
            "   - Hottest Chains (Chains עם הכי הרבה פעילות)\n"
            "   - Stock Screener עם Greek Exposure\n"
            "\n"
            "API Endpoints רלוונטיים:\n"
            "GET /api/stock/{ticker}/greek-exposure\n"
            "GET /api/stock/{ticker}/flow-alerts\n"
            "GET /api/stock/{ticker}/spot-exposures\n"
            "GET /api/option-trades/flow-alerts\n"
            "GET /api/market/market-tide\n"
            "GET /api/stock/{ticker}/max-pain"
        ),
    },
]


class GEXKnowledgeLoader:
    """Upserts the static GEX knowledge base into the strategy_knowledge collection."""

    SOURCE_TAG = "gex_knowledge_base"

    def __init__(self) -> None:
        self.ltm = LongTermMemory()
        # Public handle on the ChromaDB collection — LongTermMemory hides this behind
        # ``_coll``; we reach in once here so the rest of the file looks tidy.
        self.collection_strategy = self.ltm._coll("strategy_knowledge")

    # ───────────────────────── public ─────────────────────────

    async def load_all_knowledge(self) -> int:
        logger.info("📚 טוען ידע GEX ל-ChromaDB...")
        return await asyncio.to_thread(self._load_sync)

    def query_gex_knowledge(self, query: str, n_results: int = 3) -> list[dict[str, Any]]:
        if not query or not query.strip():
            return []
        try:
            results = self.collection_strategy.query(
                query_texts=[query],
                n_results=max(1, n_results),
                where={"source": self.SOURCE_TAG},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("GEX RAG query failed: %s", exc)
            return []

        docs = (results.get("documents") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]
        return [
            {
                "content": doc,
                "topic": (meta or {}).get("topic"),
                "category": (meta or {}).get("category"),
            }
            for doc, meta in zip(docs, metas)
        ]

    # ───────────────────────── internals ─────────────────────────

    def _load_sync(self) -> int:
        documents = [item["content"] for item in GEX_KNOWLEDGE_BASE]
        metadatas = [
            {
                "topic": item["topic"],
                "category": item["category"],
                "source": self.SOURCE_TAG,
                "language": "hebrew",
            }
            for item in GEX_KNOWLEDGE_BASE
        ]
        ids = [item["id"] for item in GEX_KNOWLEDGE_BASE]

        try:
            self.collection_strategy.upsert(
                documents=documents, metadatas=metadatas, ids=ids
            )
        except Exception:  # noqa: BLE001
            logger.exception("GEX knowledge upsert failed")
            return 0

        logger.info("✅ סה\"כ נטענו %d פריטי ידע", len(GEX_KNOWLEDGE_BASE))
        return len(GEX_KNOWLEDGE_BASE)


__all__ = ["GEXKnowledgeLoader", "GEX_KNOWLEDGE_BASE"]
