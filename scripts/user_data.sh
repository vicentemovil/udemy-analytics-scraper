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

# Set up S3 logging FIRST
echo "🔧 Setting up S3 logging..."
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
LOGS_BUCKET="ai-executor-logs-$AWS_ACCOUNT_ID"
RESULTS_BUCKET="ai-executor-results-$AWS_ACCOUNT_ID"

# Get environment variables from tags EARLY
echo "🔍 Loading environment variables from tags..."
export GOOGLE_API_KEY=$(aws ec2 describe-tags --filters "Name=resource-id,Values=$INSTANCE_ID" "Name=key,Values=GOOGLE_API_KEY" --query "Tags[0].Value" --output text --region $REGION)
export TASK_PROMPT=$(aws ec2 describe-tags --filters "Name=resource-id,Values=$INSTANCE_ID" "Name=key,Values=TASK_PROMPT" --query "Tags[0].Value" --output text --region $REGION)
export INSTANCE_NAME=$(aws ec2 describe-tags --filters "Name=resource-id,Values=$INSTANCE_ID" "Name=key,Values=INSTANCE_NAME" --query "Tags[0].Value" --output text --region $REGION)
export IMAGE_TAG=$(aws ec2 describe-tags --filters "Name=resource-id,Values=$INSTANCE_ID" "Name=key,Values=IMAGE_TAG" --query "Tags[0].Value" --output text --region $REGION)

echo "   IMAGE_TAG: $IMAGE_TAG"
echo "   INSTANCE_NAME: $INSTANCE_NAME"

# Create buckets and start continuous log upload
aws s3 mb s3://$LOGS_BUCKET --region $REGION 2>/dev/null || echo "Logs bucket exists"
aws s3 mb s3://$RESULTS_BUCKET --region $REGION 2>/dev/null || echo "Results bucket exists"

# Background process to continuously upload logs to S3
(
    while true; do
        if [ -f /tmp/execution.log ]; then
            aws s3 cp /tmp/execution.log s3://$LOGS_BUCKET/$INSTANCE_NAME.log --region $REGION 2>/dev/null || true
        fi
        sleep 5
    done
) &

echo "✅ S3 logging started - logs will stream to s3://$LOGS_BUCKET/$INSTANCE_NAME.log"

# Update system
echo "🔄 Updating system..."
apt-get update -y

# Install Docker
curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=installing_docker" 2>/dev/null || true
echo "🔄 Installing Docker..."
apt-get install -y docker.io awscli
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
    echo "🔄 Terminating instance due to Docker pull failure..."
    shutdown -h now
    echo "✅ Instance shutdown requested"
    exit 1
fi

# AUTOMATION_SCRIPT_PLACEHOLDER

# Run the automation task in Docker with real-time logging
curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=starting_automation&task=$TASK_PROMPT" 2>/dev/null || true
echo "🎯 Running automation task..."
echo "Task: $TASK_PROMPT"
echo "Docker command: docker run --rm -v /tmp:/tmp -e GOOGLE_API_KEY=*** $DOCKER_IMAGE python3 /tmp/automation_task.py"

# Check if automation script exists
if [ ! -f /tmp/automation_task.py ]; then
    echo "❌ ERROR: /tmp/automation_task.py does not exist!"
    echo "🔄 Shutting down instance due to missing script..."
    shutdown -h now
    exit 1
fi

echo "✅ Automation script exists, size: $(wc -l /tmp/automation_task.py)"

# Run Docker container with real-time logging
docker run --rm \
    -v /tmp:/tmp \
    -e GOOGLE_API_KEY="$GOOGLE_API_KEY" \
    -e AWS_ACCESS_KEY_ID \
    -e AWS_SECRET_ACCESS_KEY \
    -e AWS_SESSION_TOKEN \
    -e AWS_DEFAULT_REGION=$REGION \
    $DOCKER_IMAGE python3 /tmp/automation_task.py "$TASK_PROMPT" "$INSTANCE_NAME" "$REGION" 2>&1 | while read line; do
        echo "🐳 $line"
    done

DOCKER_EXIT_CODE=${PIPESTATUS[0]}

if [ $DOCKER_EXIT_CODE -eq 0 ]; then
    echo "✅ Docker task completed successfully"
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=docker_success&exit_code=$DOCKER_EXIT_CODE" 2>/dev/null || true
else
    echo "❌ Docker task failed with exit code: $DOCKER_EXIT_CODE"
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=docker_failed&exit_code=$DOCKER_EXIT_CODE" 2>/dev/null || true
    echo "🔄 Terminating instance due to Docker failure..."
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
shutdown -h now

echo "✅ Task completed - instance shutting down"