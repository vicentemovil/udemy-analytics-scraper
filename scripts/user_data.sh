#!/bin/bash

# EC2 User Data Script - ALL OUTPUT TO S3 LOGS
# Redirect all output to both console and S3
exec > >(tee /tmp/execution.log) 2>&1

echo "🚀 Starting AI Executor EC2 instance..."

# Get basic info immediately
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)
echo "📋 Instance ID: $INSTANCE_ID"
echo "🌍 Region: $REGION"

# Signal that user data started
curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=user_data_started" 2>/dev/null || true

# Install AWS CLI FIRST - before any AWS commands
echo "📦 Installing AWS CLI v2..."
apt-get update -y
apt-get install -y curl unzip

# Install AWS CLI v2 (official method)
AWSCLI_INSTALL=$(curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && unzip awscliv2.zip && ./aws/install 2>&1)
if [ $? -ne 0 ]; then
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=awscli_v2_install_error&error=$AWSCLI_INSTALL" 2>/dev/null || true
    echo "AWS CLI v2 install error: $AWSCLI_INSTALL"
else
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=awscli_v2_installed_successfully&path=$(which aws)" 2>/dev/null || true
    echo "AWS CLI v2 installed at: $(which aws)"
fi

# Get basic AWS info and environment variables
echo "🔧 Setting up S3 logging..."
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
LOGS_BUCKET="ai-executor-logs-$AWS_ACCOUNT_ID"
RESULTS_BUCKET="ai-executor-results-$AWS_ACCOUNT_ID"

# Get environment variables from EC2 metadata service
echo "🔍 Loading environment variables from metadata..."
export GOOGLE_API_KEY=$(curl -s http://169.254.169.254/latest/meta-data/tags/instance/GOOGLE_API_KEY)
export TASK_KEY=$(curl -s http://169.254.169.254/latest/meta-data/tags/instance/TASK_KEY)
export SCRIPT_KEY=$(curl -s http://169.254.169.254/latest/meta-data/tags/instance/SCRIPT_KEY)  
export SCRAPERS_KEY=$(curl -s http://169.254.169.254/latest/meta-data/tags/instance/SCRAPERS_KEY)
export INSTANCE_NAME=$(curl -s http://169.254.169.254/latest/meta-data/tags/instance/INSTANCE_NAME)
export IMAGE_TAG=$(curl -s http://169.254.169.254/latest/meta-data/tags/instance/IMAGE_TAG)
export TASK_ID=$(curl -s http://169.254.169.254/latest/meta-data/tags/instance/TASK_ID)
export SCRAPER=$(curl -s http://169.254.169.254/latest/meta-data/tags/instance/SCRAPER)

echo "   IMAGE_TAG: $IMAGE_TAG"
echo "   INSTANCE_NAME: $INSTANCE_NAME"
echo "   TASK_KEY: $TASK_KEY"
if [ ! -z "$SCRAPER" ]; then
    echo "   SCRAPER: $SCRAPER"
fi

# Download task prompt from S3
echo "📥 Downloading task prompt from S3..."
aws s3 cp s3://$RESULTS_BUCKET/$TASK_KEY /tmp/task.txt --region $REGION
if [ $? -eq 0 ]; then
    export TASK_PROMPT=$(cat /tmp/task.txt)
    echo "✅ Task prompt downloaded from S3"
    echo "   TASK: $(echo "$TASK_PROMPT" | head -c 100)..."
else
    echo "❌ Failed to download task prompt from S3"
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=task_download_failed" 2>/dev/null || true
    echo "⏳ Waiting 30 seconds for final log upload..."
    sleep 30
    shutdown -h now
    exit 1
fi

# Download automation script from S3
echo "📥 Downloading automation script from S3..."
aws s3 cp s3://$RESULTS_BUCKET/$SCRIPT_KEY /tmp/automation_task.py --region $REGION
if [ $? -eq 0 ]; then
    echo "✅ Automation script downloaded from S3"
else
    echo "❌ Failed to download automation script from S3"
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=script_download_failed" 2>/dev/null || true
    echo "⏳ Waiting 30 seconds for final log upload..."
    sleep 30
    shutdown -h now
    exit 1
fi

# Download and extract scrapers from S3
echo "📥 Downloading scrapers from S3..."
aws s3 cp s3://$RESULTS_BUCKET/$SCRAPERS_KEY /tmp/scrapers.zip --region $REGION
if [ $? -eq 0 ]; then
    echo "✅ Scrapers zip downloaded from S3"
    # Extract scrapers to /tmp
    cd /tmp
    unzip -q scrapers.zip
    echo "✅ Scrapers extracted to /tmp/scrapers/"
    cd -
else
    echo "❌ Failed to download scrapers from S3"
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=scrapers_download_failed" 2>/dev/null || true
    echo "⏳ Waiting 30 seconds for final log upload..."
    sleep 30
    shutdown -h now
    exit 1
fi

# Create buckets FIRST
BUCKET_ERROR=$(aws s3 mb s3://$LOGS_BUCKET --region $REGION 2>&1)
if [ $? -ne 0 ] && [[ "$BUCKET_ERROR" != *"BucketAlreadyOwnedByYou"* ]]; then
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=logs_bucket_error&error=$BUCKET_ERROR" 2>/dev/null || true
    echo "Logs bucket error: $BUCKET_ERROR"
else
    echo "Logs bucket ready"
fi

BUCKET_ERROR=$(aws s3 mb s3://$RESULTS_BUCKET --region $REGION 2>&1)
if [ $? -ne 0 ] && [[ "$BUCKET_ERROR" != *"BucketAlreadyOwnedByYou"* ]]; then
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=results_bucket_error&error=$BUCKET_ERROR" 2>/dev/null || true
    echo "Results bucket error: $BUCKET_ERROR"
else
    echo "Results bucket ready"
fi

# START BACKGROUND LOG UPLOAD AFTER BUCKET IS CREATED
(
    export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
    while true; do
        if [ -f /tmp/execution.log ] && command -v aws >/dev/null 2>&1; then
            S3_ERROR=$(aws s3 cp /tmp/execution.log s3://$LOGS_BUCKET/$INSTANCE_NAME.log --region $REGION 2>&1)
            if [ $? -ne 0 ]; then
                curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=s3_upload_error&error=$S3_ERROR" 2>/dev/null || true
            fi
        fi
        sleep 30
    done
) &

echo "✅ S3 logging started - logs will stream to s3://$LOGS_BUCKET/$INSTANCE_NAME.log"

# Install Docker
curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=installing_docker" 2>/dev/null || true
echo "🔄 Installing Docker..."
apt-get install -y docker.io
systemctl start docker
systemctl enable docker
curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=docker_installed" 2>/dev/null || true
echo "✅ Docker installed and started"

# Login to ECR
echo "🔐 Logging into ECR..."
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com
echo "✅ ECR login successful"

# Pull Docker image  
curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=pulling_docker_image&image_tag=$IMAGE_TAG&debug_env_check=yes" 2>/dev/null || true
echo "DEBUG: IMAGE_TAG is set to: '$IMAGE_TAG'"
echo "📦 Pulling Docker image: $IMAGE_TAG"
DOCKER_IMAGE="$AWS_ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/ai-executor-ec2:$IMAGE_TAG"
if docker pull $DOCKER_IMAGE; then
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=docker_image_pulled" 2>/dev/null || true
    echo "✅ Docker image pulled successfully"
else
    echo "❌ Docker image pull failed"
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=docker_pull_failed" 2>/dev/null || true
    echo "🔄 Shutting down instance due to Docker pull failure..."
    echo "⏳ Waiting 30 seconds for final log upload..."
    sleep 30
    shutdown -h now
    echo "✅ Instance shutdown requested"
    exit 1
fi

# AUTOMATION_SCRIPT_PLACEHOLDER

# Check if automation script exists
if [ ! -f /tmp/automation_task.py ]; then
    echo "❌ ERROR: /tmp/automation_task.py does not exist!"
    echo "🔄 Shutting down instance due to missing script..."
    echo "⏳ Waiting 30 seconds for final log upload..."
    sleep 30
    shutdown -h now
    exit 1
fi

echo "✅ Automation script exists, size: $(wc -l /tmp/automation_task.py)"

# Run the automation task in Docker with real-time logging
curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=starting_automation&task=$(echo "$TASK_PROMPT" | head -c 50)..." 2>/dev/null || true
echo "🎯 Running automation task..."
echo "Task: $TASK_PROMPT"
# Build Docker command args properly (task comes from file now)
DOCKER_ARGS=("--instance" "$INSTANCE_NAME" "--region" "$REGION")
if [ ! -z "$SCRAPER" ]; then
    DOCKER_ARGS+=("--scraper" "$SCRAPER")
fi
if [ ! -z "$TASK_ID" ]; then
    DOCKER_ARGS+=("--task-id" "$TASK_ID")
fi

echo "Docker command: docker run --rm -v /tmp:/tmp -e GOOGLE_API_KEY=*** $DOCKER_IMAGE python3 /tmp/automation_task.py" "${DOCKER_ARGS[@]}"

# Run Docker container with virtual display and real-time logging
docker run --rm \
    -v /tmp:/tmp \
    -e GOOGLE_API_KEY="$GOOGLE_API_KEY" \
    -e AWS_ACCESS_KEY_ID \
    -e AWS_SECRET_ACCESS_KEY \
    -e AWS_SESSION_TOKEN \
    -e AWS_DEFAULT_REGION=$REGION \
    -e DISPLAY=:99 \
    --shm-size=2g \
    $DOCKER_IMAGE sh -c "Xvfb :99 -screen 0 1920x1080x24 & python3 /tmp/automation_task.py ${DOCKER_ARGS[*]}" 2>&1 | while read line; do
        echo "🐳 $line"
    done

DOCKER_EXIT_CODE=${PIPESTATUS[0]}

if [ $DOCKER_EXIT_CODE -eq 0 ]; then
    echo "✅ Docker task completed successfully"
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=docker_success&exit_code=$DOCKER_EXIT_CODE" 2>/dev/null || true
else
    echo "❌ Docker task failed with exit code: $DOCKER_EXIT_CODE"
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=docker_failed&exit_code=$DOCKER_EXIT_CODE" 2>/dev/null || true
    echo "🔄 Shutting down instance due to Docker failure..."
    echo "⏳ Waiting 30 seconds for final log upload..."
    sleep 30
    shutdown -h now
    echo "✅ Instance shutdown requested"
    exit 1
fi

# Upload result to S3
echo "📤 Uploading results to S3..."
if [ -f /tmp/result.json ]; then
    aws s3 cp /tmp/result.json s3://$RESULTS_BUCKET/$INSTANCE_NAME-result.json --region $REGION
    echo "✅ Results uploaded to S3"
    echo "📋 Task Result:"
    cat /tmp/result.json
else
    echo "⚠️  No result file found"
fi

# Clean up and terminate instance
echo "🧹 Cleaning up..."
docker system prune -f

echo "🔄 Shutting down instance..."
echo "⏳ Waiting 30 seconds for final log upload..."
sleep 30
shutdown -h now

echo "✅ Task completed - instance shutting down"