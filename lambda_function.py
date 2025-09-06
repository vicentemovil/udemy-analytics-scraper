import asyncio
import json
import logging
import os
from dotenv import load_dotenv
from browser_use import Agent, BrowserSession
from langchain_google_genai import ChatGoogleGenerativeAI

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_browser_task(prompt=None):
    """
    Run browser task with given prompt
    """
    try:
        # Initialize LLM
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-pro",
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
            temperature=0.1
        )
        
        # Create browser session (headless for Lambda)
        browser_session = BrowserSession(
            headless=True,  # Must be headless in Lambda
            executable_path="/opt/python/chromium",  # Playwright installed path
            keep_alive=False
        )
        
        await browser_session.start()
        logger.info("✅ Browser started")
        
        # Use provided prompt - let AI decide where to navigate
        instruction = prompt
        agent = Agent(
            task=instruction,
            llm=llm,
            browser_session=browser_session
        )
        
        # Run the task (same as old app)
        result = await agent.run(max_steps=10)
        final_result = result.final_result() if result else "Task completed"
        
        # Close browser
        await browser_session.close()
        logger.info("✅ Browser closed")
        
        return {
            "status": "success",
            "task": instruction,
            "result": str(final_result)
        }
        
    except Exception as e:
        logger.error(f"Task failed: {e}")
        return {
            "status": "error",
            "error": str(e)
        }

def lambda_handler(event, context):
    """
    AWS Lambda handler
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    # Extract prompt from event
    prompt = event.get('prompt') or event.get('instruction')
    
    # Run the async task
    result = asyncio.run(run_browser_task(prompt))
    
    return {
        'statusCode': 200 if result['status'] == 'success' else 500,
        'headers': {
            'Content-Type': 'application/json',
        },
        'body': json.dumps(result)
    }