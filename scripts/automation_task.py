import asyncio
import json
import os
import boto3
from browser_use import Agent, BrowserSession
from langchain_google_genai import ChatGoogleGenerativeAI

async def run_task(prompt, instance_name, region):
    try:
        # Initialize LLM
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-pro",
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
            temperature=0.1
        )
        
        # Create browser session for EC2
        browser_session = BrowserSession(
            headless=True,
            keep_alive=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ],
            user_data_dir="/app/chrome-user-data"
        )
        
        await browser_session.start()
        print("✅ Browser started")
        
        # Create agent and run task
        agent = Agent(
            task=prompt,
            llm=llm,
            browser_session=browser_session
        )
        
        result = await agent.run(max_steps=10)
        final_result = result.final_result() if result else "Task completed"
        
        await browser_session.close()
        print("✅ Browser closed")
        
        return {
            "status": "success",
            "task": prompt,
            "result": str(final_result)
        }
        
    except Exception as e:
        print(f"❌ Task failed: {e}")
        return {
            "status": "error",
            "error": str(e)
        }

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 4:
        print("Usage: python automation_task.py 'prompt' 'instance_name' 'region'")
        sys.exit(1)
    
    prompt = sys.argv[1]
    instance_name = sys.argv[2]
    region = sys.argv[3]
    
    result = asyncio.run(run_task(prompt, instance_name, region))
    
    # Save result to file
    with open("/tmp/result.json", "w") as f:
        json.dump(result, f, indent=2)
    
    print("✅ Task completed - result saved to /tmp/result.json")