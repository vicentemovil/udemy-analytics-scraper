#!/usr/bin/env python3
"""
Deploy Lambda function, execute with prompt, get result, then cleanup
Uses basic IAM credentials (AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)
"""

import boto3
import json
import time
import subprocess
import sys
import os
import random
from dotenv import load_dotenv
from botocore.exceptions import ClientError

# Load environment variables
load_dotenv()

# Configuration
REGION = os.environ.get("AWS_REGION", "us-east-1")
FUNCTION_NAME = f"ai-executor-{random.randint(1000, 9999)}"
REPOSITORY_NAME = f"ai-executor-{random.randint(1000, 9999)}"
IMAGE_TAG = "latest"

def run_command(command, description):
    """Run shell command with error handling"""
    print(f"🔄 {description}...")
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} completed")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed: {e.stderr}")
        return None

def check_aws_credentials():
    """Verify AWS credentials are set"""
    access_key = os.environ.get('AWS_ACCESS_KEY_ID')
    secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    
    if not access_key or not secret_key:
        print("❌ AWS credentials not found!")
        print("Set environment variables:")
        print("export AWS_ACCESS_KEY_ID=your_access_key")
        print("export AWS_SECRET_ACCESS_KEY=your_secret_key")
        sys.exit(1)
    
    print("✅ AWS credentials found")

def create_ecr_repository():
    """Create ECR repository if it doesn't exist"""
    ecr = boto3.client('ecr', region_name=REGION)
    
    try:
        # Check if repository exists
        ecr.describe_repositories(repositoryNames=[REPOSITORY_NAME])
        print(f"✅ ECR repository '{REPOSITORY_NAME}' already exists")
    except ClientError as e:
        if e.response['Error']['Code'] == 'RepositoryNotFoundException':
            # Create repository
            print(f"🔄 Creating ECR repository '{REPOSITORY_NAME}'...")
            ecr.create_repository(repositoryName=REPOSITORY_NAME)
            print(f"✅ ECR repository '{REPOSITORY_NAME}' created")
        else:
            print(f"❌ Error checking repository: {e}")
            sys.exit(1)

def build_and_push_image():
    """Build Docker image and push to ECR"""
    account_id = boto3.client('sts').get_caller_identity()['Account']
    ecr_uri = f"{account_id}.dkr.ecr.{REGION}.amazonaws.com/{REPOSITORY_NAME}:{IMAGE_TAG}"
    
    # Login to ECR
    result = run_command(
        f"aws ecr get-login-password --region {REGION} | docker login --username AWS --password-stdin {account_id}.dkr.ecr.{REGION}.amazonaws.com",
        "Logging in to ECR"
    )
    if not result:
        sys.exit(1)
    
    # Build image
    result = run_command(
        f"docker build -t {REPOSITORY_NAME}:{IMAGE_TAG} .",
        "Building Docker image"
    )
    if not result:
        sys.exit(1)
    
    # Tag image
    result = run_command(
        f"docker tag {REPOSITORY_NAME}:{IMAGE_TAG} {ecr_uri}",
        "Tagging Docker image"
    )
    if not result:
        sys.exit(1)
    
    # Push image
    result = run_command(
        f"docker push {ecr_uri}",
        "Pushing Docker image to ECR"
    )
    if not result:
        sys.exit(1)
    
    return ecr_uri

def create_lambda_execution_role():
    """Create IAM role for Lambda execution"""
    iam = boto3.client('iam')
    role_name = f"{FUNCTION_NAME}-execution-role"
    
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "lambda.amazonaws.com"
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }
    
    try:
        # Check if role exists
        role = iam.get_role(RoleName=role_name)
        print(f"✅ IAM role '{role_name}' already exists")
        return role['Role']['Arn'], role_name
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            # Create role
            print(f"🔄 Creating IAM role '{role_name}'...")
            role = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Execution role for AI Executor Lambda"
            )
            
            # Attach basic Lambda execution policy
            iam.attach_role_policy(
                RoleName=role_name,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            )
            
            print(f"✅ IAM role '{role_name}' created")
            
            # Wait for role to be available
            print("⏳ Waiting for role to be ready...")
            time.sleep(10)
            
            return role['Role']['Arn'], role_name
        else:
            print(f"❌ Error creating role: {e}")
            sys.exit(1)

def create_lambda_function(image_uri, role_arn):
    """Create Lambda function"""
    lambda_client = boto3.client('lambda', region_name=REGION)
    
    print(f"🔄 Creating Lambda function '{FUNCTION_NAME}'...")
    try:
        response = lambda_client.create_function(
            FunctionName=FUNCTION_NAME,
            Role=role_arn,
            Code={'ImageUri': image_uri},
            PackageType='Image',
            Timeout=900,  # 15 minutes
            MemorySize=3008,  # Maximum memory
            Environment={
                'Variables': {
                    'GOOGLE_API_KEY': os.environ.get('GOOGLE_API_KEY', ''),
                }
            },
            Description="AI Executor using browser automation"
        )
        print(f"✅ Lambda function '{FUNCTION_NAME}' created")
        return response
        
    except Exception as e:
        print(f"❌ Error creating Lambda function: {e}")
        sys.exit(1)

def invoke_lambda_function(prompt):
    """Execute the Lambda function with given prompt"""
    lambda_client = boto3.client('lambda', region_name=REGION)
    
    print(f"🔄 Executing task: '{prompt}'...")
    try:
        response = lambda_client.invoke(
            FunctionName=FUNCTION_NAME,
            InvocationType='RequestResponse',
            Payload=json.dumps({"prompt": prompt})
        )
        
        payload = json.loads(response['Payload'].read())
        print(f"✅ Task execution completed")
        
        if payload.get('statusCode') == 200:
            body = json.loads(payload['body'])
            print(f"📊 Result: {body.get('result', 'No result')}")
            return body
        else:
            print(f"❌ Task failed: {payload}")
            return None
        
    except Exception as e:
        print(f"❌ Task execution failed: {e}")
        return None

def cleanup_resources(role_name=None):
    """Delete Lambda function, IAM role, and ECR repository"""
    print("\n🧹 Starting cleanup...")
    
    # Delete Lambda function
    try:
        lambda_client = boto3.client('lambda', region_name=REGION)
        lambda_client.delete_function(FunctionName=FUNCTION_NAME)
        print(f"✅ Lambda function '{FUNCTION_NAME}' deleted")
    except Exception as e:
        print(f"⚠️  Could not delete Lambda function: {e}")
    
    # Delete IAM role
    if role_name:
        try:
            iam = boto3.client('iam')
            # Detach policies first
            iam.detach_role_policy(
                RoleName=role_name,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            )
            # Delete role
            iam.delete_role(RoleName=role_name)
            print(f"✅ IAM role '{role_name}' deleted")
        except Exception as e:
            print(f"⚠️  Could not delete IAM role: {e}")
    
    # Delete ECR repository
    try:
        ecr = boto3.client('ecr', region_name=REGION)
        ecr.delete_repository(repositoryName=REPOSITORY_NAME, force=True)
        print(f"✅ ECR repository '{REPOSITORY_NAME}' deleted")
    except Exception as e:
        print(f"⚠️  Could not delete ECR repository: {e}")
    
    print("✅ Cleanup completed")

def main():
    """Main execution function"""
    # Get prompt from user
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    else:
        prompt = input("Enter your AI task prompt: ").strip()
        if not prompt:
            print("❌ Prompt is required!")
            sys.exit(1)
    
    print(f"🚀 Starting AI Executor...")
    print(f"📝 Task: {prompt}")
    print(f"🏷️  Function: {FUNCTION_NAME}")
    print(f"📦 Repository: {REPOSITORY_NAME}")
    
    role_name = None
    
    try:
        # Check prerequisites
        check_aws_credentials()
        
        # Create ECR repository
        create_ecr_repository()
        
        # Build and push Docker image
        image_uri = build_and_push_image()
        
        # Create IAM role
        role_arn, role_name = create_lambda_execution_role()
        
        # Create Lambda function
        create_lambda_function(image_uri, role_arn)
        
        # Wait for function to be ready
        print("⏳ Waiting for function to be ready...")
        time.sleep(30)
        
        # Execute the task
        result = invoke_lambda_function(prompt)
        
        if result and result.get('status') == 'success':
            print(f"\n🎉 Task completed successfully!")
            print(f"📄 Final Result: {result.get('result')}")
        else:
            print(f"\n⚠️  Task had issues - check the logs above")
    
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
    finally:
        # Always cleanup resources
        cleanup_resources(role_name)
        print("\n✅ All done!")

if __name__ == "__main__":
    main()