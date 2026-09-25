"""Take verification screenshots. Requires: pip install playwright && python -m playwright install chromium
Usage: python scripts/screenshots.py [base_url]   (default http://localhost:8765/)"""
import sys, asyncio
from pathlib import Path
from playwright.async_api import async_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/"
OUT = Path(__file__).resolve().parent.parent / "screenshots"
async def main():
    OUT.mkdir(exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        shots = [("mobile", dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True), "dark"),
                 ("desktop", dict(viewport={"width": 1280, "height": 800}), "dark"),
                 ("mobile-light", dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True), "light")]
        errors = []
        for name, ctx_opts, theme in shots:
            ctx = await b.new_context(**ctx_opts, timezone_id="Asia/Kolkata", locale="en-IN")
            await ctx.add_init_script(f"localStorage.setItem('aidaily-theme','{theme}')")
            page = await ctx.new_page()
            page.on("console", lambda m: m.type == "error" and errors.append(m.text))
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(BASE, wait_until="networkidle")
            await page.wait_for_selector(".card")
            await page.wait_for_timeout(800)
            await page.screenshot(path=str(OUT / f"{name}.png"), full_page=(name == "desktop-full"))
            await ctx.close()
        await b.close()
        print("console errors:", errors or "none")
asyncio.run(main())
