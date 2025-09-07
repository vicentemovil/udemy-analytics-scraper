#!/usr/bin/env python3
"""
Flask API for managing multiple AI agent deployments with UUID tracking
"""

import os
import json
import uuid
import threading
import time
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from pathlib import Path
import subprocess
import sys

app = Flask(__name__)

# Ensure results and logs directories exist
RESULTS_DIR = Path("results")
LOGS_DIR = Path("logs")
RESULTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

def get_task_file(task_id):
    """Get the path to a task's JSON file"""
    return RESULTS_DIR / f"{task_id}.json"

def load_task(task_id):
    """Load task from its JSON file"""
    task_file = get_task_file(task_id)
    if task_file.exists():
        try:
            with open(task_file, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return None

def save_task(task_id, task_data):
    """Save task to its JSON file"""
    task_file = get_task_file(task_id)
    try:
        with open(task_file, 'w') as f:
            json.dump(task_data, f, indent=2)
        return True
    except Exception:
        return False

def update_task_status(task_id, status, **kwargs):
    """Update task status and save to file"""
    task = load_task(task_id)
    if task:
        task["status"] = status
        if status == "deploying" and "started_at" not in task:
            task["started_at"] = datetime.now().isoformat()
        elif status in ["completed", "failed"] and "completed_at" not in task:
            task["completed_at"] = datetime.now().isoformat()
        
        # Add any additional fields
        task.update(kwargs)
        save_task(task_id, task)

def run_agent_deployment(task_id, prompt, scraper=None):
    """Run AI agent deployment in a separate thread"""
    try:
        update_task_status(task_id, "deploying")
        
        # Create log file for this task in logs directory
        log_file = LOGS_DIR / f"{task_id}.txt"
        
        # Build command with task_id
        cmd = [sys.executable, "deploy_ai_agent.py", "--task", prompt, "--task-id", task_id]
        if scraper:
            cmd.extend(["--scraper", scraper])
        
        print(f"🚀 Starting deployment for task {task_id}")
        print(f"📝 Logs will be written to: {log_file}")
        print(f"🔧 Command: {' '.join(cmd)}")
        
        # Run the deployment with real-time output capture
        with open(log_file, 'w') as log:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Combine stderr with stdout
                text=True,
                bufsize=1,  # Line buffered
                universal_newlines=True,
                cwd=os.getcwd()
            )
            
            # Stream output to both console and file in real-time
            for line in iter(process.stdout.readline, ''):
                if line:
                    line_clean = line.rstrip()
                    # Print to console with task ID prefix
                    print(f"[{task_id[:8]}] {line_clean}")
                    # Write to log file
                    log.write(line)
                    log.flush()
            
            # Wait for process to complete
            return_code = process.wait()
        
        # Update final status based on return code
        if return_code == 0:
            update_task_status(task_id, "completed", return_code=return_code)
            print(f"✅ Task {task_id} completed successfully")
        else:
            update_task_status(task_id, "failed", return_code=return_code)
            print(f"❌ Task {task_id} failed with return code {return_code}")
    
    except Exception as e:
        update_task_status(task_id, "failed", error=str(e))
        print(f"❌ Task {task_id} failed with exception: {e}")

@app.route('/launch', methods=['POST'])
def launch_agent():
    """Launch a new AI agent deployment"""
    data = request.get_json()
    
    if not data or 'prompt' not in data:
        return jsonify({"error": "Missing 'prompt' in request body"}), 400
    
    prompt = data['prompt']
    scraper = data.get('scraper')  # Optional
    
    # Generate UUID for this task
    task_id = str(uuid.uuid4())
    
    # Create initial task JSON file
    task_data = {
        "id": task_id,
        "prompt": prompt,
        "scraper": scraper,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "started_at": None,
        "completed_at": None
    }
    
    # Save initial task to file
    save_task(task_id, task_data)
    
    # Start deployment in separate thread
    thread = threading.Thread(
        target=run_agent_deployment,
        args=(task_id, prompt, scraper),
        daemon=True
    )
    thread.start()
    
    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "message": "AI agent deployment started"
    }), 202

@app.route('/status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """Get the status of a specific task"""
    task = load_task(task_id)
    
    if not task:
        return jsonify({"error": "Task not found"}), 404
    
    return jsonify(task)

@app.route('/status', methods=['GET'])
def get_all_tasks():
    """Get the status of all tasks with pagination, filtering, and sorting"""
    # Get query parameters
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 10))
    status_filter = request.args.get('status')  # queued, deploying, completed, failed
    scraper_filter = request.args.get('scraper')
    sort_by = request.args.get('sort', 'created_at')  # created_at, status, completed_at
    sort_order = request.args.get('order', 'desc')  # asc, desc
    
    tasks = []
    
    # Scan results directory for all task files
    for task_file in RESULTS_DIR.glob("*.json"):
        try:
            with open(task_file, 'r') as f:
                task_data = json.load(f)
                tasks.append(task_data)
        except Exception:
            continue
    
    # Apply filters
    if status_filter:
        tasks = [t for t in tasks if t.get('status') == status_filter]
    
    if scraper_filter:
        tasks = [t for t in tasks if t.get('scraper') == scraper_filter]
    
    # Sort tasks
    reverse = sort_order == 'desc'
    try:
        if sort_by in ['created_at', 'started_at', 'completed_at']:
            tasks.sort(key=lambda x: x.get(sort_by) or '', reverse=reverse)
        elif sort_by == 'status':
            tasks.sort(key=lambda x: x.get('status', ''), reverse=reverse)
        else:
            # Default sort by created_at desc (newest first)
            tasks.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    except Exception:
        # Fallback to default sort
        tasks.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    
    # Pagination
    total = len(tasks)
    start = (page - 1) * per_page
    end = start + per_page
    paginated_tasks = tasks[start:end]
    
    # Calculate pagination info
    total_pages = (total + per_page - 1) // per_page
    has_next = page < total_pages
    has_prev = page > 1
    
    return jsonify({
        "tasks": paginated_tasks,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "has_next": has_next,
            "has_prev": has_prev,
            "next_page": page + 1 if has_next else None,
            "prev_page": page - 1 if has_prev else None
        },
        "filters": {
            "status": status_filter,
            "scraper": scraper_filter,
            "sort": sort_by,
            "order": sort_order
        }
    })

@app.route('/results/<task_id>', methods=['GET'])
def get_task_results(task_id):
    """Get the results of a specific task"""
    task = load_task(task_id)
    
    if not task:
        return jsonify({"error": "Task not found"}), 404
    
    return jsonify(task)

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    # Count active tasks by scanning files
    active_count = 0
    total_count = 0
    
    for task_file in RESULTS_DIR.glob("*.json"):
        try:
            with open(task_file, 'r') as f:
                task_data = json.load(f)
                total_count += 1
                if task_data.get("status") in ["queued", "deploying"]:
                    active_count += 1
        except Exception:
            continue
    
    return jsonify({
        "status": "healthy",
        "active_tasks": active_count,
        "total_tasks": total_count
    })

@app.route('/scrapers', methods=['GET'])
def get_available_scrapers():
    """Get list of available scrapers"""
    scrapers_dir = Path("scripts/scrapers")
    scrapers = []
    
    if scrapers_dir.exists():
        for scraper_file in scrapers_dir.glob("*.py"):
            if scraper_file.name != "__init__.py":
                scraper_name = scraper_file.stem
                
                # Try to read the scraper file to get description
                description = None
                try:
                    with open(scraper_file, 'r') as f:
                        content = f.read()
                        # Look for docstring or comments with description
                        lines = content.split('\n')
                        for line in lines[:10]:  # Check first 10 lines
                            if '"""' in line or "'''" in line:
                                description = line.replace('"""', '').replace("'''", '').strip()
                                break
                            elif line.strip().startswith('#') and len(line.strip()) > 5:
                                description = line.strip()[1:].strip()
                                break
                except Exception:
                    pass
                
                scrapers.append({
                    "name": scraper_name,
                    "description": description or f"{scraper_name} scraper",
                    "file": str(scraper_file)
                })
    
    return jsonify({
        "scrapers": scrapers,
        "total": len(scrapers)
    })

@app.route('/logs/<task_id>', methods=['GET'])
def get_task_logs(task_id):
    """Get logs for a specific task"""
    log_file = LOGS_DIR / f"{task_id}.txt"
    
    if not log_file.exists():
        return jsonify({"error": "Log file not found"}), 404
    
    try:
        # Get tail parameter (default last 100 lines)
        tail = int(request.args.get('tail', 100))
        
        with open(log_file, 'r') as f:
            lines = f.readlines()
            
        # Return last N lines
        if tail > 0:
            lines = lines[-tail:]
            
        return jsonify({
            "task_id": task_id,
            "log_file": str(log_file),
            "lines": len(lines),
            "content": ''.join(lines)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', methods=['GET'])
def dashboard():
    """Serve the dashboard HTML"""
    return render_template('dashboard.html')

if __name__ == "__main__":
    print("🚀 Starting AI Agent Management API")
    print("📁 Results will be saved to: results/")
    print("🌐 API Endpoints:")
    print("   GET  /             - Dashboard (Web UI)")
    print("   POST /launch       - Launch new AI agent")
    print("   GET  /status       - Get all tasks (paginated, filtered, sorted)")
    print("   GET  /status/<id>  - Get specific task status")
    print("   GET  /results/<id> - Get task results")
    print("   GET  /scrapers     - Get available scrapers")
    print("   GET  /health       - Health check")
    print("")
    print("🌐 Open http://localhost:5000 in your browser to access the dashboard")
    
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)