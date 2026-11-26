"""Browser agent: fills the approved values with Playwright and STOPS. It never clicks submit."""

BANNER = ("document.body.insertAdjacentHTML('afterbegin',\"<div style='position:fixed;z-index:99999;"
          "top:0;left:0;right:0;background:#166534;color:#fff;padding:8px 14px;font:14px sans-serif'>"
          "VaultPilot filled the highlighted fields. Check them, enter any OTP/CAPTCHA yourself, "
          "then submit.</div>\")")


def fill(url: str, items: list, headless: bool = False, wait_for_close: bool = True) -> dict:
    """items: [{selector, tag, value}]. Returns what each field contains after filling."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(url)
        for it in items:
            loc = page.locator(it["selector"]).first
            loc.scroll_into_view_if_needed()
            if it["tag"] == "select":
                try:
                    loc.select_option(value=it["value"])
                except Exception:
                    loc.select_option(label=it["value"])
            else:
                loc.fill(it["value"])
            loc.evaluate("e => e.style.outline = '3px solid #16a34a'")
            page.wait_for_timeout(200)
        page.evaluate(BANNER)
        result = {it["selector"]: page.locator(it["selector"]).first.input_value() for it in items}
        if wait_for_close and not headless:
            page.wait_for_event("close", timeout=0)
        browser.close()
        return result
