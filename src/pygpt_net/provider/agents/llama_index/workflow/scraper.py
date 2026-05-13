#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# This file is a part of PYGPT package               #
# Website: https://pygpt.net                         #
# GitHub:  https://github.com/szczyglis-dev/py-gpt   #
# MIT License                                        #
# Created By  : Marcin Szczygliński                  #
# Updated Date: 2026.05.13 00:00:00                  #
# ================================================== #

"""
Scraper Agent Workflow
----------------------
Browser-capable scraping agent with Playwright. Supports:
  - General web page scraping
  - Google Maps place/business data extraction
  - Public Facebook page content extraction
  - Screenshot capture

The agent uses FunctionAgent with a set of Playwright-backed tools and runs
as a standard LlamaIndex Workflow compatible with the existing Runner.
"""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from llama_index.core.agent.workflow import FunctionAgent, AgentStream, AgentOutput
from llama_index.core.tools import FunctionTool
from llama_index.core.workflow import (
    Context,
    Event,
    StartEvent,
    StopEvent,
    Workflow,
    step,
)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SCRAPER_SYSTEM_PROMPT = """
You are a Browser Scraper Agent. You have access to tools that control a real
Chromium browser via Playwright. Use them to:

1. Navigate to URLs and extract structured information
2. Query Google Maps for place data (address, rating, reviews, phone, hours)
3. Extract content from public Facebook pages
4. Take screenshots for visual inspection
5. Fill forms and click buttons when needed

Always:
- Return clean, structured JSON when extracting data
- Respect robots.txt and avoid rate-limiting targets
- Never enter credentials or personal data into forms
- Summarise findings clearly in the user's language
"""


# ---------------------------------------------------------------------------
# Playwright tool implementations
# ---------------------------------------------------------------------------

def _get_or_create_browser_context():
    """
    Return a (browser, context, page) triple. Uses a module-level singleton
    so multiple tool calls within the same run share the same browser session.
    """
    # Lazy import — Playwright is an optional heavy dependency
    from playwright.sync_api import sync_playwright
    return sync_playwright


class BrowserTools:
    """Namespace for all Playwright-backed scraping tools."""

    def __init__(self, headless: bool = True, timeout_ms: int = 30_000):
        self._headless = headless
        self._timeout = timeout_ms
        self._playwright = None
        self._browser = None
        self._page = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Launch browser (called before any tool use in this run)."""
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self._headless)
            self._page = self._browser.new_page()
            self._page.set_default_timeout(self._timeout)
        except Exception as e:
            raise RuntimeError(f"Failed to start Playwright browser: {e}") from e

    def stop(self):
        """Close browser session."""
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._browser = None
            self._playwright = None
            self._page = None

    @property
    def page(self):
        if self._page is None:
            self.start()
        return self._page

    # ------------------------------------------------------------------
    # General web scraping
    # ------------------------------------------------------------------

    def navigate_and_extract(self, url: str, selector: str = "body") -> str:
        """
        Navigate to a URL and extract text content from the given CSS selector.

        :param url: Full URL to visit
        :param selector: CSS selector to extract (default: entire body)
        :return: Extracted text content (max 8000 chars)
        """
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(1500)
            element = self.page.query_selector(selector)
            if element is None:
                return f"Selector '{selector}' not found on {url}"
            text = element.inner_text()
            return text[:8000]
        except Exception as e:
            return f"Error scraping {url}: {e}"

    def get_page_links(self, url: str, filter_text: str = "") -> str:
        """
        Get all hyperlinks from a page, optionally filtered by link text.

        :param url: URL to visit
        :param filter_text: Only return links whose text contains this string
        :return: JSON list of {text, href} objects
        """
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(1000)
            links = self.page.eval_on_selector_all(
                "a[href]",
                "els => els.map(el => ({text: el.innerText.trim(), href: el.href}))"
            )
            if filter_text:
                ft = filter_text.lower()
                links = [l for l in links if ft in l.get("text", "").lower()]
            return json.dumps(links[:100], ensure_ascii=False)
        except Exception as e:
            return f"Error getting links from {url}: {e}"

    def take_screenshot(self, url: str, save_path: str = "/tmp/screenshot.png") -> str:
        """
        Navigate to URL and save a screenshot.

        :param url: URL to screenshot
        :param save_path: File path to save PNG
        :return: Confirmation message with path
        """
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
            self.page.screenshot(path=save_path, full_page=True)
            return f"Screenshot saved to {save_path}"
        except Exception as e:
            return f"Error taking screenshot of {url}: {e}"

    def click_and_extract(self, url: str, click_selector: str, extract_selector: str = "body") -> str:
        """
        Navigate to URL, click an element, then extract text.

        :param url: URL to visit
        :param click_selector: CSS selector of element to click
        :param extract_selector: CSS selector to extract after click
        :return: Extracted text (max 8000 chars)
        """
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(1000)
            self.page.click(click_selector)
            self.page.wait_for_timeout(2000)
            element = self.page.query_selector(extract_selector)
            if element is None:
                return f"Selector '{extract_selector}' not found after click"
            return element.inner_text()[:8000]
        except Exception as e:
            return f"Error in click_and_extract: {e}"

    # ------------------------------------------------------------------
    # Google Maps
    # ------------------------------------------------------------------

    def google_maps_search(self, query: str, max_results: int = 5) -> str:
        """
        Search Google Maps for places/businesses matching the query.

        :param query: Search query (e.g. "pizza restaurants Warsaw")
        :param max_results: Maximum number of results to return
        :return: JSON list of place objects with name, address, rating, etc.
        """
        try:
            encoded = query.replace(" ", "+")
            url = f"https://www.google.com/maps/search/{encoded}"
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(3000)

            # Accept cookies if dialog appears
            try:
                self.page.click("button:has-text('Accept all')", timeout=3000)
                self.page.wait_for_timeout(1000)
            except Exception:
                pass

            # Extract result cards
            results = []
            cards = self.page.query_selector_all("[data-result-index]")[:max_results]
            for card in cards:
                try:
                    name = card.query_selector("[class*='fontHeadlineSmall']")
                    rating = card.query_selector("[class*='MW4etd']")
                    reviews = card.query_selector("[class*='UY7F9']")
                    address = card.query_selector("[class*='W4Efsd']")
                    results.append({
                        "name": name.inner_text() if name else "",
                        "rating": rating.inner_text() if rating else "",
                        "reviews": reviews.inner_text() if reviews else "",
                        "address": address.inner_text() if address else "",
                    })
                except Exception:
                    pass

            # Fallback: extract raw text from results panel
            if not results:
                panel = self.page.query_selector("[role='feed']")
                if panel:
                    raw = panel.inner_text()[:4000]
                    return f"Raw results:\n{raw}"

            return json.dumps(results, ensure_ascii=False)
        except Exception as e:
            return f"Error searching Google Maps for '{query}': {e}"

    def google_maps_place_details(self, place_name: str, address: str = "") -> str:
        """
        Get detailed information about a specific place on Google Maps.

        :param place_name: Name of the place
        :param address: Optional address to narrow the search
        :return: JSON with place details: address, phone, hours, website, rating
        """
        try:
            query = f"{place_name} {address}".strip().replace(" ", "+")
            url = f"https://www.google.com/maps/search/{query}"
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(3000)

            # Accept cookies
            try:
                self.page.click("button:has-text('Accept all')", timeout=2000)
                self.page.wait_for_timeout(800)
            except Exception:
                pass

            # Click first result
            try:
                first = self.page.query_selector("[data-result-index='0']")
                if first:
                    first.click()
                    self.page.wait_for_timeout(3000)
            except Exception:
                pass

            details = {}

            def _text(sel):
                el = self.page.query_selector(sel)
                return el.inner_text().strip() if el else ""

            # Try common Maps detail selectors
            details["name"] = _text("h1")
            details["rating"] = _text("[class*='fontDisplayLarge']")
            details["address"] = _text("[data-item-id='address'] .fontBodyMedium")
            details["phone"] = _text("[data-item-id*='phone'] .fontBodyMedium")
            details["website"] = _text("[data-item-id='authority'] .fontBodyMedium")
            details["hours"] = _text("[data-item-id*='oh'] .fontBodyMedium")

            # Remove empty keys
            details = {k: v for k, v in details.items() if v}
            return json.dumps(details, ensure_ascii=False)
        except Exception as e:
            return f"Error fetching place details for '{place_name}': {e}"

    # ------------------------------------------------------------------
    # Facebook (public pages only)
    # ------------------------------------------------------------------

    def facebook_public_page(self, page_url: str) -> str:
        """
        Extract publicly visible content from a Facebook page.
        Only accesses public/non-login-required content.

        :param page_url: Full URL of the Facebook page
        :return: Extracted text content (max 8000 chars)
        """
        try:
            # Use mbasic for better text extraction without JS
            url = page_url.replace("www.facebook.com", "mbasic.facebook.com")
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)

            # Extract main content
            content_parts = []

            # Page name / title
            header = self.page.query_selector("h1")
            if header:
                content_parts.append(f"Page: {header.inner_text()}")

            # Posts
            posts = self.page.query_selector_all("div[data-ft]")[:10]
            for post in posts:
                text = post.inner_text().strip()
                if text and len(text) > 20:
                    content_parts.append(text[:500])

            # Fallback: get all text
            if not content_parts:
                body = self.page.query_selector("body")
                if body:
                    content_parts.append(body.inner_text()[:6000])

            result = "\n\n---\n\n".join(content_parts)
            return result[:8000] if result else "No public content found"
        except Exception as e:
            return f"Error fetching Facebook page '{page_url}': {e}"

    def facebook_search(self, query: str) -> str:
        """
        Search for public Facebook content using mbasic interface.

        :param query: Search query
        :return: Search results text (max 6000 chars)
        """
        try:
            encoded = query.replace(" ", "+")
            url = f"https://mbasic.facebook.com/search/posts?q={encoded}"
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
            body = self.page.query_selector("body")
            if body:
                return body.inner_text()[:6000]
            return "No results found"
        except Exception as e:
            return f"Error searching Facebook for '{query}': {e}"


# ---------------------------------------------------------------------------
# Build LlamaIndex FunctionTools from BrowserTools
# ---------------------------------------------------------------------------

def build_scraper_tools(browser_tools: BrowserTools) -> List[FunctionTool]:
    """Create LlamaIndex FunctionTools wrapping BrowserTools methods."""
    tools = []

    def _wrap(fn, name, desc):
        import inspect
        sig = inspect.signature(fn)
        params = {
            k: v for k, v in sig.parameters.items()
            if k != "self"
        }
        return FunctionTool.from_defaults(fn=fn, name=name, description=desc)

    tools.append(_wrap(
        browser_tools.navigate_and_extract,
        "web_scrape",
        "Navigate to a URL and extract text content. Args: url (str), selector (str, optional CSS selector, default 'body'). Returns extracted text.",
    ))
    tools.append(_wrap(
        browser_tools.get_page_links,
        "get_links",
        "Get all hyperlinks from a web page. Args: url (str), filter_text (str, optional). Returns JSON list of {text, href}.",
    ))
    tools.append(_wrap(
        browser_tools.take_screenshot,
        "screenshot",
        "Take a screenshot of a web page. Args: url (str), save_path (str, optional). Returns confirmation.",
    ))
    tools.append(_wrap(
        browser_tools.click_and_extract,
        "click_and_extract",
        "Click an element on a page then extract content. Args: url (str), click_selector (str), extract_selector (str, optional).",
    ))
    tools.append(_wrap(
        browser_tools.google_maps_search,
        "maps_search",
        "Search Google Maps for places. Args: query (str), max_results (int, optional). Returns JSON list of places.",
    ))
    tools.append(_wrap(
        browser_tools.google_maps_place_details,
        "maps_place_details",
        "Get detailed info for a specific place on Google Maps. Args: place_name (str), address (str, optional).",
    ))
    tools.append(_wrap(
        browser_tools.facebook_public_page,
        "facebook_page",
        "Extract public content from a Facebook page. Args: page_url (str, full Facebook URL). Returns page content.",
    ))
    tools.append(_wrap(
        browser_tools.facebook_search,
        "facebook_search",
        "Search public Facebook content. Args: query (str). Returns search results text.",
    ))
    return tools


# ---------------------------------------------------------------------------
# Workflow events
# ---------------------------------------------------------------------------

class ScraperInputEvent(StartEvent):
    query: str
    headless: bool = True


class ScraperStepEvent(Event):
    message: str


# ---------------------------------------------------------------------------
# Scraper Workflow
# ---------------------------------------------------------------------------

class ScraperWorkflow(Workflow):
    """
    LlamaIndex Workflow wrapping a browser-enabled FunctionAgent.
    Manages browser lifecycle: opens on start, closes on stop.
    """

    def __init__(self, **kwargs):
        super().__init__(
            timeout=kwargs.get("timeout", 180),
            verbose=kwargs.get("verbose", False),
        )
        self._llm = kwargs["llm"]
        self._system_prompt = kwargs.get("system_prompt", SCRAPER_SYSTEM_PROMPT)
        self._extra_tools: List = kwargs.get("extra_tools", [])
        self._headless: bool = kwargs.get("headless", True)
        self._max_steps: int = kwargs.get("max_steps", 10)
        self._browser_tools: Optional[BrowserTools] = None

    @step
    async def run_agent(self, ctx: Context, ev: StartEvent) -> StopEvent:
        query = getattr(ev, "query", str(ev))

        # Start browser
        self._browser_tools = BrowserTools(headless=self._headless)
        try:
            self._browser_tools.start()
        except Exception as e:
            return StopEvent(result=f"Browser failed to start: {e}")

        scraper_tools = build_scraper_tools(self._browser_tools)
        all_tools = scraper_tools + list(self._extra_tools)

        agent = FunctionAgent(
            tools=all_tools,
            llm=self._llm,
            system_prompt=self._system_prompt,
            max_function_calls=self._max_steps,
            verbose=getattr(ctx, "_verbose", False),
        )

        try:
            response = await agent.run(query)
            result = str(response)
        except Exception as e:
            result = f"Scraper agent error: {e}"
        finally:
            self._browser_tools.stop()

        return StopEvent(result=result)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_workflow(window, kwargs: Dict[str, Any]) -> ScraperWorkflow:
    """
    Build a ScraperWorkflow from the agent kwargs dict passed by Runner.

    :param window: Window instance
    :param kwargs: Agent keyword arguments from Runner.call()
    :return: ScraperWorkflow instance
    """
    llm = kwargs.get("llm")
    system_prompt = kwargs.get("system_prompt", SCRAPER_SYSTEM_PROMPT)
    extra_tools = list(kwargs.get("tools", []) or [])
    verbose = kwargs.get("verbose", False)
    max_steps = kwargs.get("max_iterations", 10)

    # Read headless setting from preset options or global config
    headless = True
    preset = kwargs.get("preset")
    if preset and hasattr(preset, "extra"):
        headless = preset.extra.get("scraper", {}).get("headless", True)

    return ScraperWorkflow(
        llm=llm,
        system_prompt=system_prompt,
        extra_tools=extra_tools,
        verbose=verbose,
        max_steps=max_steps,
        headless=headless,
    )
