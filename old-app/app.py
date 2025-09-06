import asyncio
import logging
import os
from flask import Flask, request, jsonify
from browser_use import Agent, BrowserSession
from langchain_google_genai import ChatGoogleGenerativeAI
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
browser_session = None
llm = None
loop = None

def find_chrome_path():
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe")
    ]
    for path in chrome_paths:
        if os.path.exists(path):
            return path
    return None

async def setup_browser():
    global browser_session, llm
    
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-pro",
        google_api_key="AIzaSyCHAGe4sRlsIsVJWn8nNpuVSBquYb2NYMA",
        temperature=0.1
    )
    
    chrome_path = find_chrome_path()
    
    browser_session = BrowserSession(
        headless=False,
        executable_path=chrome_path,
        keep_alive=True,
        user_data_dir="./chrome_data"
    )
    
    await browser_session.start()
    page = await browser_session.get_current_page()
    await page.goto("https://google.com")
    logger.info("✅ Browser connected!")

async def run_agent_task(instruction):
    agent = Agent(
        task=instruction,
        llm=llm,
        browser_session=browser_session
    )
    result = await agent.run(max_steps=10)
    return result

def start_event_loop():
    global loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(setup_browser())
    loop.run_forever()

@app.route('/task', methods=['POST'])
def run_task():
    instruction = request.json.get('instruction', '')
    logger.info(f"Running: {instruction}")
    
    try:
        # Increase timeout to 1800 seconds (30 minutes)
        future = asyncio.run_coroutine_threadsafe(run_agent_task(instruction), loop)
        result = future.result(timeout=1800)
        
        final_result = result.final_result() if result else "Task completed"
        
        return jsonify({
            "status": "success",
            "instruction": instruction,
            "result": str(final_result)
        })
        
    except asyncio.TimeoutError:
        return jsonify({"error": "Task timed out after 30 minutes"}), 408
        
    except Exception as e:
        logger.error(f"Task failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/')
def home():
    return jsonify({"status": "ready"})

if __name__ == '__main__':
    # Start async loop in background thread
    thread = threading.Thread(target=start_event_loop, daemon=True)
    thread.start()
    
    # Wait for browser to be ready
    import time
    time.sleep(5)
    
    # Start Flask
    app.run(host='0.0.0.0', port=5000)