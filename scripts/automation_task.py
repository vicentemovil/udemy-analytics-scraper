import asyncio
import json
import os
import boto3
import logging
import sys
from browser_use import Agent, BrowserSession
from browser_use.llm import ChatGoogle

# Set up logging to show detailed progress
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Enable detailed logging for browser_use library
logging.getLogger("browser_use").setLevel(logging.INFO)
logging.getLogger("browser_use.agent").setLevel(logging.DEBUG)
logging.getLogger("langchain").setLevel(logging.INFO)

async def run_task(prompt, instance_name, region, scraper=None):
    try:
        logger.info(f"🚀 Starting task: {prompt}")
        logger.info(f"📋 Instance: {instance_name}, Region: {region}")
        
        # Initialize LLM
        logger.info("🔧 Initializing LLM...")
        llm = ChatGoogle(
            model="gemini-2.5-flash",
            api_key=os.environ.get("GOOGLE_API_KEY"),
            temperature=0.1
        )
        logger.info("✅ LLM initialized")
        
        # Debug: Check which browser-use package we're using
        try:
            import browser_use
            logger.info(f"🔍 Browser-use package: {browser_use.__file__}")
            logger.info(f"🔍 Browser-use version: {getattr(browser_use, '__version__', 'unknown')}")
        except Exception as e:
            logger.warning(f"⚠️ Could not check browser-use package: {e}")
        
        # Create browser session for EC2 with Cloudflare bypass (following re-browser-use example exactly)
        logger.info("🌐 Creating browser session with anti-detection...")
        from browser_use import BrowserProfile
        
        browser_session = BrowserSession(
            browser_profile=BrowserProfile(
                headless=False,  # Critical for OS-level clicks to bypass Cloudflare
                disable_security=False,
                cross_origin_iframes=True,
                highlight_elements=True
            ),
            keep_alive=False,
            user_data_dir="/app/chrome-user-data"
        )
        
        logger.info("🚀 Starting browser...")
        await browser_session.start()
        logger.info("✅ Browser started successfully")
        
        # Create agent and run task
        logger.info("🤖 Creating AI agent...")
        agent = Agent(
            task=prompt,
            llm=llm,
            browser_session=browser_session
        )
        
        logger.info("🎯 Running automation task...")
        
        # Run automation task
        result = await agent.run(max_steps=10)
        
        final_result = result.final_result() if result else "Task completed"
        logger.info(f"✅ Task completed: {final_result}")
        
        # Get final page state
        current_url = "Unknown"
        try:
            # Use the correct browser-use API methods
            current_url = await browser_session.get_current_page_url()
            logger.info(f"🌐 Final page URL: {current_url}")
            
        except Exception as e:
            logger.warning(f"⚠️ Could not extract page info: {e}")
        
        # Run scraper if specified
        if scraper:
            logger.info(f"🔄 Running scraper: {scraper}")
            try:
                # Dynamically import and run the scraper
                scraper_module = __import__(f"scrapers.{scraper}", fromlist=[scraper])
                run_scraper_func = getattr(scraper_module, f"run_{scraper}_scraper")
                await run_scraper_func(instance_name, region)
                logger.info(f"✅ Scraper completed")
            except ImportError:
                logger.error(f"❌ Scraper not found: {scraper}")
            except AttributeError as e:
                logger.error(f"❌ Scraper function not found: run_{scraper}_scraper")
            except Exception as e:
                logger.error(f"❌ Scraper failed: {e}")
        
        logger.info("✅ Browser task completed - session will auto-cleanup")
        
        result_data = {
            "status": "success",
            "task": prompt,
            "result": str(final_result),
            "final_url": current_url
        }
        
        return result_data
        
    except Exception as e:
        logger.error(f"❌ Task failed: {e}")
        # Ensure we exit with error code so Docker/script knows it failed
        import traceback
        logger.error(f"🔍 Full traceback: {traceback.format_exc()}")
        
        # Always return error result for upload
        return {
            "status": "error",
            "task": prompt,
            "error": str(e),
            "traceback": traceback.format_exc()
        }

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Browser automation task')
    parser.add_argument('--instance', default='local-instance', help='Instance name (optional)')
    parser.add_argument('--region', default='us-east-1', help='AWS region (optional)')  
    parser.add_argument('--scraper', help='Run scraper after automation task (optional)')
    parser.add_argument('--task-id', help='Task UUID for result upload (optional)')
    
    args = parser.parse_args()
    
    # Read task prompt from file instead of argument
    try:
        with open('/tmp/task.txt', 'r') as f:
            prompt = f.read().strip()
        logger.info(f"📥 Task loaded from file: {prompt[:100]}...")
    except Exception as e:
        logger.error(f"❌ Failed to read task from file: {e}")
        sys.exit(1)
    
    instance_name = args.instance
    region = args.region
    scraper = args.scraper
    task_id = args.task_id or instance_name  # Fallback to instance_name if no task_id
    
    logger.info("🎬 Starting automation script...")
    
    # Always get a result, even if there's an exception
    result = None
    try:
        result = asyncio.run(run_task(prompt, instance_name, region, scraper))
    except Exception as top_level_error:
        logger.error(f"❌ Top-level script error: {top_level_error}")
        import traceback
        result = {
            "status": "error",
            "task": prompt,
            "error": str(top_level_error),
            "traceback": traceback.format_exc()
        }
    
    # ALWAYS save and upload result
    logger.info("💾 Saving result to file...")
    with open("/tmp/result.json", "w") as f:
        json.dump(result, f, indent=2)
    
    # ALWAYS upload result to S3 - no matter what happened
    logger.info("📤 Uploading result to S3...")
    try:
        import boto3
        s3 = boto3.client('s3')
        account_id = boto3.client('sts').get_caller_identity()['Account']
        results_bucket = f"ai-executor-results-{account_id}"
        result_key = f"{task_id}-result.json"
        
        s3.upload_file("/tmp/result.json", results_bucket, result_key)
        logger.info(f"✅ Result uploaded to s3://{results_bucket}/{result_key}")
    except Exception as upload_error:
        logger.error(f"❌ Failed to upload result to S3: {upload_error}")
        # Still exit properly even if upload fails
    
    # Exit with proper code based on result status
    if result and result.get("status") == "error":
        logger.error("❌ Script exiting with error code 1")
        sys.exit(1)
    else:
        logger.info("✅ Script completed - result saved and uploaded")
        sys.exit(0)