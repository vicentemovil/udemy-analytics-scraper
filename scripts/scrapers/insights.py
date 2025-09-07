#!/usr/bin/env python3

import asyncio
import logging
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

async def run_insights_scraper(instance_name, region):
    """
    Download content from Udemy Insights API using Playwright with authenticated Chrome user data
    """
    api_content = None
    api_url = "https://www.udemy.com/api-2.0/marketplace/current/insights/?search=media+interviewer+skills&language=en&fields[course]=@default,headline,image_304x171,instructor_name,content_info,num_published_lectures,content_length_practice_test_questions,num_published_practice_tests,instructional_level,discount,num_reviews,rating,badges,caption_languages"
    
    try:
        logger.info(f"📥 Launching Playwright Chromium with same user data to download API content...")
        
        # Use Playwright to launch Chromium with the same user data directory
        async with async_playwright() as p:
            # Launch browser with same user data directory and virtual display
            browser = await p.chromium.launch_persistent_context(
                user_data_dir="/app/chrome-user-data",
                headless=False,  # Use virtual display
                args=[
                    "--display=:99",  # Use virtual display
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            
            try:
                # Create a new page
                page = await browser.new_page()
                
                # Navigate to API URL
                logger.info(f"🌐 Navigating to API URL...")
                await page.goto(api_url, wait_until="networkidle", timeout=30000)
                
                # Wait a bit more for content to load
                await asyncio.sleep(3)
                
                # Get complete page content - no truncation, preserve exactly as browser shows
                try:
                    # Get the raw body text content (preserves JSON structure)
                    body_text = await page.evaluate("document.body.innerText || document.body.textContent")
                    if body_text:
                        api_content = body_text  # Keep complete content, no stripping
                        logger.info(f"✅ Downloaded complete content ({len(api_content)} characters) from API endpoint")
                    else:
                        # Fall back to full HTML if no body text
                        api_content = await page.content()
                        logger.info(f"✅ Downloaded complete HTML content ({len(api_content)} characters) from API endpoint")
                except:
                    # Last resort - get all content
                    api_content = await page.content()
                    logger.info(f"✅ Downloaded complete HTML content ({len(api_content)} characters) from API endpoint")
                
                
            finally:
                # Always close the browser
                await browser.close()
                
    except Exception as e:
        logger.warning(f"⚠️ Could not download API content with Playwright: {e}")
        api_content = f"Error downloading content: {str(e)}"
    
    return {
        "scraper": "insights",
        "status": "success" if api_content and "Error" not in api_content else "error",
        "content_length": len(api_content) if api_content else 0,
        "api_url": api_url
    }