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
Browser-capable scraping agent with dual-backend support:

  Backend A — agent-browser (preferred, if installed)
    CLI tool by Vercel Labs (https://github.com/vercel-labs/agent-browser).
    Uses Chrome DevTools Protocol with deterministic element refs (@e1, @e2),
    JSON snapshot output, semantic locators, and annotated screenshots.
    Designed specifically for LLM/AI-agent consumption.
    Install: npm install -g agent-browser && agent-browser install

  Backend B — Playwright (fallback)
    Direct Playwright sync_api automation.  No extra install needed
    (already a project dependency).

The ScraperWorkflow auto-detects which backend is available and builds the
appropriate FunctionTools.  Both expose the same logical tool names so the
FunctionAgent behaves identically regardless of backend.

Supported capabilities:
  - General web page scraping / element extraction
  - Google Maps place/business data extraction
  - Public Facebook page content extraction
  - Screenshot capture (annotated for agent-browser)
"""

import json
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.tools import FunctionTool
from llama_index.core.workflow import (
    StartEvent,
    StopEvent,
    Workflow,
    step,
)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SCRAPER_SYSTEM_PROMPT = """
You are a Browser Scraper Agent. You control a real Chromium browser to:

1. Navigate to URLs and extract structured information
2. Query Google Maps for place data (address, rating, reviews, phone, hours)
3. Extract content from public Facebook pages
4. Take screenshots for visual inspection
5. Interact with pages: click buttons, fill forms, follow links

When using agent-browser backend you receive structured element snapshots with
stable refs like @e1, @e2.  Use these refs for precise interaction instead of
guessing CSS selectors.

Always:
- Return clean, structured JSON when extracting data
- Respect robots.txt and avoid rate-limiting targets
- Never enter real credentials or personal data into forms
- Summarise findings clearly in the user's language
"""


# ---------------------------------------------------------------------------
# Backend A: agent-browser CLI tools
# ---------------------------------------------------------------------------

class AgentBrowserTools:
    """
    Wraps the agent-browser CLI (https://github.com/vercel-labs/agent-browser).
    Uses subprocess to invoke each command; the daemon handles persistence.
    """

    def __init__(self, timeout: int = 30):
        self._timeout = timeout
        self._bin = "agent-browser"

    def start(self):
        """Verify agent-browser is accessible (no persistent state to open)."""
        result = subprocess.run(
            [self._bin, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"agent-browser responded with exit {result.returncode}: {result.stderr.strip()}"
            )

    def stop(self):
        """No persistent browser process to close for agent-browser."""
        pass

    def _run(self, *args: str) -> str:
        """Run an agent-browser command and return stdout."""
        cmd = [self._bin] + list(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            if result.returncode != 0 and result.stderr:
                return f"Error: {result.stderr.strip()[:500]}"
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return f"Timeout after {self._timeout}s running: {' '.join(cmd)}"
        except FileNotFoundError:
            return "agent-browser not found. Install with: npm install -g agent-browser && agent-browser install"
        except Exception as e:
            return f"Subprocess error: {e}"

    def _batch(self, *commands: str) -> str:
        """Run multiple commands in a single agent-browser batch call (faster)."""
        return self._run("batch", *commands)

    # ------------------------------------------------------------------
    # General web scraping
    # ------------------------------------------------------------------

    def web_scrape(self, url: str, selector: str = "") -> str:
        """
        Navigate to a URL and return a structured JSON snapshot of interactive
        elements, or extract the text of a specific element by CSS selector.

        :param url: Full URL to visit
        :param selector: Optional CSS selector — if empty returns full interactive snapshot
        :return: JSON element snapshot or extracted text (max 8000 chars)
        """
        if selector:
            output = self._batch(
                f"open {url}",
                f"get text {selector}",
            )
        else:
            output = self._batch(
                f"open {url}",
                "snapshot --interactive --json",
            )
        return output[:8000]

    def get_links(self, url: str, filter_text: str = "") -> str:
        """
        Get all hyperlinks from a page, optionally filtered by link text.

        :param url: URL to visit
        :param filter_text: Only return links whose text contains this (case-insensitive)
        :return: JSON list of {text, href}
        """
        output = self._batch(
            f"open {url}",
            "find role link snapshot --json",
        )
        if filter_text and output:
            try:
                data = json.loads(output)
                ft = filter_text.lower()
                data = [el for el in data if ft in str(el.get("text", "")).lower()]
                return json.dumps(data[:100], ensure_ascii=False)
            except Exception:
                pass
        return output[:6000]

    def screenshot(self, url: str, save_path: str = "/tmp/screenshot.png") -> str:
        """
        Navigate to URL and save an annotated screenshot (element refs overlaid).

        :param url: URL to screenshot
        :param save_path: File path to save PNG
        :return: Confirmation message with path
        """
        output = self._batch(
            f"open {url}",
            f"screenshot --annotate --output {save_path}",
        )
        return f"Screenshot saved to {save_path}\n{output}"

    def click_and_extract(self, url: str, element_ref: str, extract_selector: str = "") -> str:
        """
        Navigate to URL, click an element by agent-browser ref (@e1) or semantic
        locator, then extract resulting page content.

        :param url: URL to visit
        :param element_ref: Element ref like @e3, or semantic like 'role button --name Submit'
        :param extract_selector: Optional CSS selector to extract after click
        :return: Page snapshot or extracted text after click
        """
        click_cmd = f"click {element_ref}"
        if extract_selector:
            extract_cmd = f"get text {extract_selector}"
        else:
            extract_cmd = "snapshot --interactive --json"
        output = self._batch(
            f"open {url}",
            click_cmd,
            extract_cmd,
        )
        return output[:8000]

    def fill_form(self, url: str, element_ref: str, value: str) -> str:
        """
        Navigate to URL and fill a form field with a value.

        :param url: URL to visit
        :param element_ref: Element ref like @e2, or semantic like 'label "Email"'
        :param value: Value to type into the field
        :return: Snapshot after fill
        """
        output = self._batch(
            f"open {url}",
            f"fill {element_ref} {json.dumps(value)}",
            "snapshot --interactive --json",
        )
        return output[:8000]

    # ------------------------------------------------------------------
    # Google Maps
    # ------------------------------------------------------------------

    def google_maps_search(self, query: str, max_results: int = 5) -> str:
        """
        Search Google Maps for places/businesses matching the query.

        :param query: Search query (e.g. "pizza restaurants Warsaw")
        :param max_results: Maximum number of results to return
        :return: JSON snapshot of search results
        """
        encoded = query.replace(" ", "+")
        url = f"https://www.google.com/maps/search/{encoded}"
        output = self._batch(
            f"open {url}",
            "wait 3000",
            "snapshot --json",
        )
        return output[:8000]

    def google_maps_place_details(self, place_name: str, address: str = "") -> str:
        """
        Get detailed information about a specific place on Google Maps.

        :param place_name: Name of the place
        :param address: Optional address to narrow the search
        :return: Extracted place details text
        """
        query = f"{place_name} {address}".strip().replace(" ", "+")
        url = f"https://www.google.com/maps/search/{query}"
        output = self._batch(
            f"open {url}",
            "wait 3000",
            "find role listitem click",   # click first result
            "wait 3000",
            "get text h1",
            "snapshot --json",
        )
        return output[:8000]

    # ------------------------------------------------------------------
    # Facebook (public pages only)
    # ------------------------------------------------------------------

    def facebook_public_page(self, page_url: str) -> str:
        """
        Extract publicly visible content from a Facebook page (mbasic).

        :param page_url: Full URL of the Facebook page
        :return: Extracted text content (max 8000 chars)
        """
        url = page_url.replace("www.facebook.com", "mbasic.facebook.com")
        output = self._batch(
            f"open {url}",
            "wait 2000",
            "snapshot --json",
        )
        return output[:8000]

    def facebook_search(self, query: str) -> str:
        """
        Search public Facebook content via mbasic interface.

        :param query: Search query
        :return: Search results snapshot
        """
        encoded = query.replace(" ", "+")
        url = f"https://mbasic.facebook.com/search/posts?q={encoded}"
        output = self._batch(
            f"open {url}",
            "wait 2000",
            "snapshot --json",
        )
        return output[:6000]

    def natural_language_action(self, url: str, instruction: str) -> str:
        """
        Navigate to URL and execute a natural language browser instruction via
        agent-browser's built-in AI chat mode.

        :param url: URL to visit first
        :param instruction: Natural language instruction (e.g. 'Click the login button')
        :return: Result snapshot or text
        """
        output = self._batch(
            f"open {url}",
            f"chat {json.dumps(instruction)}",
        )
        return output[:8000]


# ---------------------------------------------------------------------------
# Backend B: Playwright tools (fallback)
# ---------------------------------------------------------------------------

class PlaywrightTools:
    """Playwright sync_api browser automation — fallback when agent-browser absent."""

    def __init__(self, headless: bool = True, timeout_ms: int = 30_000):
        self._headless = headless
        self._timeout = timeout_ms
        self._playwright = None
        self._browser = None
        self._page = None

    def start(self):
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._page = self._browser.new_page()
        self._page.set_default_timeout(self._timeout)

    def stop(self):
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._playwright = None
        self._page = None

    @property
    def page(self):
        if self._page is None:
            self.start()
        return self._page

    def web_scrape(self, url: str, selector: str = "body") -> str:
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(1500)
            el = self.page.query_selector(selector)
            return (el.inner_text() if el else f"Selector '{selector}' not found")[:8000]
        except Exception as e:
            return f"Error scraping {url}: {e}"

    def get_links(self, url: str, filter_text: str = "") -> str:
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

    def screenshot(self, url: str, save_path: str = "/tmp/screenshot.png") -> str:
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
            self.page.screenshot(path=save_path, full_page=True)
            return f"Screenshot saved to {save_path}"
        except Exception as e:
            return f"Error taking screenshot: {e}"

    def click_and_extract(self, url: str, element_ref: str, extract_selector: str = "body") -> str:
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(1000)
            self.page.click(element_ref)
            self.page.wait_for_timeout(2000)
            el = self.page.query_selector(extract_selector)
            return (el.inner_text() if el else "Not found")[:8000]
        except Exception as e:
            return f"Error in click_and_extract: {e}"

    def fill_form(self, url: str, element_ref: str, value: str) -> str:
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(1000)
            self.page.fill(element_ref, value)
            self.page.wait_for_timeout(1000)
            return self.web_scrape(url, "body")
        except Exception as e:
            return f"Error in fill_form: {e}"

    def google_maps_search(self, query: str, max_results: int = 5) -> str:
        try:
            encoded = query.replace(" ", "+")
            self.page.goto(f"https://www.google.com/maps/search/{encoded}", wait_until="domcontentloaded")
            self.page.wait_for_timeout(3000)
            try:
                self.page.click("button:has-text('Accept all')", timeout=3000)
                self.page.wait_for_timeout(1000)
            except Exception:
                pass
            results = []
            cards = self.page.query_selector_all("[data-result-index]")[:max_results]
            for card in cards:
                try:
                    name = card.query_selector("[class*='fontHeadlineSmall']")
                    rating = card.query_selector("[class*='MW4etd']")
                    address = card.query_selector("[class*='W4Efsd']")
                    results.append({
                        "name": name.inner_text() if name else "",
                        "rating": rating.inner_text() if rating else "",
                        "address": address.inner_text() if address else "",
                    })
                except Exception:
                    pass
            if not results:
                panel = self.page.query_selector("[role='feed']")
                if panel:
                    return panel.inner_text()[:4000]
            return json.dumps(results, ensure_ascii=False)
        except Exception as e:
            return f"Error searching Google Maps: {e}"

    def google_maps_place_details(self, place_name: str, address: str = "") -> str:
        try:
            query = f"{place_name} {address}".strip().replace(" ", "+")
            self.page.goto(f"https://www.google.com/maps/search/{query}", wait_until="domcontentloaded")
            self.page.wait_for_timeout(3000)
            try:
                self.page.click("button:has-text('Accept all')", timeout=2000)
                self.page.wait_for_timeout(800)
            except Exception:
                pass
            try:
                first = self.page.query_selector("[data-result-index='0']")
                if first:
                    first.click()
                    self.page.wait_for_timeout(3000)
            except Exception:
                pass
            def _text(sel):
                el = self.page.query_selector(sel)
                return el.inner_text().strip() if el else ""
            details = {
                "name": _text("h1"),
                "address": _text("[data-item-id='address'] .fontBodyMedium"),
                "phone": _text("[data-item-id*='phone'] .fontBodyMedium"),
                "website": _text("[data-item-id='authority'] .fontBodyMedium"),
                "hours": _text("[data-item-id*='oh'] .fontBodyMedium"),
            }
            return json.dumps({k: v for k, v in details.items() if v}, ensure_ascii=False)
        except Exception as e:
            return f"Error fetching place details: {e}"

    def facebook_public_page(self, page_url: str) -> str:
        try:
            url = page_url.replace("www.facebook.com", "mbasic.facebook.com")
            self.page.goto(url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
            parts = []
            h = self.page.query_selector("h1")
            if h:
                parts.append(f"Page: {h.inner_text()}")
            posts = self.page.query_selector_all("div[data-ft]")[:10]
            for p in posts:
                t = p.inner_text().strip()
                if t and len(t) > 20:
                    parts.append(t[:500])
            if not parts:
                body = self.page.query_selector("body")
                if body:
                    parts.append(body.inner_text()[:6000])
            return "\n\n---\n\n".join(parts)[:8000]
        except Exception as e:
            return f"Error fetching Facebook page: {e}"

    def facebook_search(self, query: str) -> str:
        try:
            encoded = query.replace(" ", "+")
            self.page.goto(f"https://mbasic.facebook.com/search/posts?q={encoded}", wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
            body = self.page.query_selector("body")
            return body.inner_text()[:6000] if body else "No results"
        except Exception as e:
            return f"Error searching Facebook: {e}"

    def natural_language_action(self, url: str, instruction: str) -> str:
        return (
            f"natural_language_action requires agent-browser backend "
            f"(install: npm install -g agent-browser && agent-browser install). "
            f"Falling back — please use web_scrape or click_and_extract with explicit selectors."
        )


# ---------------------------------------------------------------------------
# Backend auto-detection
# ---------------------------------------------------------------------------

def _agent_browser_available() -> bool:
    """Return True if the agent-browser CLI is on PATH."""
    return shutil.which("agent-browser") is not None


def _build_tools(backend) -> List[FunctionTool]:
    """Build LlamaIndex FunctionTools from a backend instance."""
    tools = [
        FunctionTool.from_defaults(
            fn=backend.web_scrape,
            name="web_scrape",
            description=(
                "Navigate to a URL and extract content. "
                "Args: url (str), selector (str, optional — CSS selector or empty for full snapshot). "
                "Returns extracted text or JSON element snapshot."
            ),
        ),
        FunctionTool.from_defaults(
            fn=backend.get_links,
            name="get_links",
            description=(
                "Get all hyperlinks from a web page. "
                "Args: url (str), filter_text (str, optional). "
                "Returns JSON list of {text, href}."
            ),
        ),
        FunctionTool.from_defaults(
            fn=backend.screenshot,
            name="screenshot",
            description=(
                "Take a screenshot of a web page (annotated with element refs if agent-browser). "
                "Args: url (str), save_path (str, optional). Returns path confirmation."
            ),
        ),
        FunctionTool.from_defaults(
            fn=backend.click_and_extract,
            name="click_and_extract",
            description=(
                "Navigate to URL, click an element, then extract content. "
                "Args: url (str), element_ref (str — CSS selector or @e1 ref or semantic), "
                "extract_selector (str, optional). Returns content after click."
            ),
        ),
        FunctionTool.from_defaults(
            fn=backend.fill_form,
            name="fill_form",
            description=(
                "Navigate to URL and fill a form field. "
                "Args: url (str), element_ref (str — CSS selector, @e2 ref, or label), "
                "value (str). Returns page snapshot after fill."
            ),
        ),
        FunctionTool.from_defaults(
            fn=backend.google_maps_search,
            name="maps_search",
            description=(
                "Search Google Maps for places/businesses. "
                "Args: query (str), max_results (int, optional). "
                "Returns JSON list of places with name, rating, address."
            ),
        ),
        FunctionTool.from_defaults(
            fn=backend.google_maps_place_details,
            name="maps_place_details",
            description=(
                "Get detailed info for a specific place on Google Maps. "
                "Args: place_name (str), address (str, optional). "
                "Returns JSON with address, phone, website, hours."
            ),
        ),
        FunctionTool.from_defaults(
            fn=backend.facebook_public_page,
            name="facebook_page",
            description=(
                "Extract public content from a Facebook page (mbasic). "
                "Args: page_url (str, full Facebook URL). Returns page content."
            ),
        ),
        FunctionTool.from_defaults(
            fn=backend.facebook_search,
            name="facebook_search",
            description=(
                "Search public Facebook content. "
                "Args: query (str). Returns search results text."
            ),
        ),
        FunctionTool.from_defaults(
            fn=backend.natural_language_action,
            name="browser_chat",
            description=(
                "Execute a natural language browser instruction (agent-browser only). "
                "Args: url (str), instruction (str — e.g. 'Click the login button'). "
                "Returns result snapshot."
            ),
        ),
    ]
    return tools


# ---------------------------------------------------------------------------
# Workflow events
# ---------------------------------------------------------------------------

class ScraperInputEvent(StartEvent):
    query: str
    headless: bool = True


# ---------------------------------------------------------------------------
# Scraper Workflow
# ---------------------------------------------------------------------------

class ScraperWorkflow(Workflow):
    """
    LlamaIndex Workflow wrapping a browser-enabled FunctionAgent.
    Selects agent-browser CLI backend (preferred) or Playwright (fallback).
    Manages browser/daemon lifecycle around the agent run.
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
        self._force_playwright: bool = kwargs.get("force_playwright", False)
        self._backend = None

    @step
    async def run_agent(self, ctx, ev: StartEvent) -> StopEvent:
        query = getattr(ev, "query", str(ev))

        # Select backend
        use_agent_browser = (not self._force_playwright) and _agent_browser_available()
        backend_name = "agent-browser" if use_agent_browser else "Playwright"

        if use_agent_browser:
            self._backend = AgentBrowserTools()
        else:
            self._backend = PlaywrightTools(headless=self._headless)

        try:
            self._backend.start()
        except Exception as e:
            return StopEvent(result=f"Browser failed to start ({backend_name}): {e}")

        scraper_tools = _build_tools(self._backend)
        all_tools = scraper_tools + list(self._extra_tools)

        agent = FunctionAgent(
            tools=all_tools,
            llm=self._llm,
            system_prompt=self._system_prompt + f"\n\n[Backend: {backend_name}]",
            max_function_calls=self._max_steps,
            verbose=getattr(ctx, "_verbose", False),
        )

        try:
            response = await agent.run(query)
            result = str(response)
        except Exception as e:
            result = f"Scraper agent error ({backend_name}): {e}"
        finally:
            if self._backend:
                self._backend.stop()

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

    headless = True
    force_playwright = False
    preset = kwargs.get("preset")
    if preset and hasattr(preset, "extra"):
        scraper_opts = preset.extra.get("scraper", {})
        headless = scraper_opts.get("headless", True)
        force_playwright = scraper_opts.get("force_playwright", False)

    return ScraperWorkflow(
        llm=llm,
        system_prompt=system_prompt,
        extra_tools=extra_tools,
        verbose=verbose,
        max_steps=max_steps,
        headless=headless,
        force_playwright=force_playwright,
    )
