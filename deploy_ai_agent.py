#!/usr/bin/env python3
"""
Deploy EC2 instance with Docker, execute browser automation, get result, then cleanup
"""

import boto3
import json
import time
import subprocess
import sys
import os
import random
import base64
from dotenv import load_dotenv
from botocore.exceptions import ClientError

# Load environment variables
load_dotenv()

# Configuration
REGION = os.environ.get("AWS_REGION", "us-east-1")
INSTANCE_NAME = f"ai-executor-{random.randint(1000, 9999)}"
REPOSITORY_NAME = "ai-executor-ec2"
INSTANCE_TYPE = "t3.medium"  # Enough power for browser automation

def get_runtime_hash():
    """Generate hash of runtime environment (Dockerfile + requirements) for versioning"""
    import hashlib
    
    hash_content = ""
    
    # Only hash the runtime environment files, not the automation code
    with open('ec2-image/Dockerfile', 'rb') as f:
        hash_content += f.read().decode()
    
    with open('ec2-image/requirements.txt', 'rb') as f:
        hash_content += f.read().decode()
    
    return hashlib.md5(hash_content.encode()).hexdigest()[:8]

# Use runtime hash for image versioning - same runtime = same tag
IMAGE_TAG = f"runtime-{get_runtime_hash()}"

def check_aws_credentials():
    """Verify AWS credentials are set"""
    access_key = os.environ.get('AWS_ACCESS_KEY_ID')
    secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    
    if not access_key or not secret_key:
        print("❌ AWS credentials not found. Please set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")
        print("   You can add them to your .env file")
        sys.exit(1)
    
    print("✅ AWS credentials found")

def build_docker_image_if_needed():
    """Build Docker image only if it doesn't exist"""
    print("🔍 Checking if Docker image exists...")
    
    try:
        ecr = boto3.client('ecr', region_name=REGION)
        images = ecr.list_images(repositoryName=REPOSITORY_NAME)
        
        for image in images.get('imageIds', []):
            if image.get('imageTag') == IMAGE_TAG:
                print(f"✅ Docker image {IMAGE_TAG} already exists - skipping build")
                return
    except ecr.exceptions.RepositoryNotFoundException:
        print("🔄 ECR repository doesn't exist - will create during build")
    except Exception as e:
        print(f"⚠️  Could not check existing images: {e}")
    
    print("🔄 Building Docker image...")
    
    # Create ECR repository if it doesn't exist
    try:
        ecr.create_repository(repositoryName=REPOSITORY_NAME)
        print(f"✅ ECR repository '{REPOSITORY_NAME}' created")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        print(f"✅ ECR repository '{REPOSITORY_NAME}' already exists")
    except Exception as e:
        print(f"❌ Failed to create ECR repository: {e}")
        sys.exit(1)
    
    # Build using AWS CodeBuild (macOS Mojave compatible)
    try:
        account_id = boto3.client('sts').get_caller_identity()['Account']
        build_docker_image_with_codebuild()
        
        # Cleanup old runtime images
        cleanup_old_images()
        
    except Exception as e:
        print(f"❌ Build process failed: {e}")
        sys.exit(1)

def cleanup_old_images():
    """Remove old runtime images (keep current one)"""
    print("🗑️  Cleaning up old runtime images...")
    try:
        ecr = boto3.client('ecr', region_name=REGION)
        images = ecr.list_images(repositoryName=REPOSITORY_NAME)
        
        current_tag = IMAGE_TAG
        
        for image in images.get('imageIds', []):
            if 'imageTag' in image:
                tag = image['imageTag']
                # Remove old runtime versions (keep current runtime)
                if tag.startswith('runtime-') and tag != current_tag:
                    try:
                        ecr.batch_delete_image(
                            repositoryName=REPOSITORY_NAME,
                            imageIds=[{'imageTag': tag}]
                        )
                        print(f"🗑️  Removed old runtime image: {tag}")
                    except Exception as e:
                        print(f"⚠️  Could not remove image {tag}: {e}")
    except Exception as e:
        print(f"⚠️  Runtime cleanup error: {e}")

def build_docker_image_with_codebuild():
    """Build Docker image using AWS CodeBuild"""
    print("🔄 Using AWS CodeBuild to build Docker image...")
    
    import zipfile
    import tempfile
    
    # Create CodeBuild project
    codebuild = boto3.client('codebuild', region_name=REGION)
    s3 = boto3.client('s3', region_name=REGION)
    account_id = boto3.client('sts').get_caller_identity()['Account']
    
    project_name = f"ai-executor-ec2-build-{random.randint(1000, 9999)}"
    bucket_name = f"ai-executor-ec2-build-{account_id}-{random.randint(1000, 9999)}"
    
    # Create S3 bucket for source
    try:
        s3.create_bucket(Bucket=bucket_name)
        print(f"✅ S3 bucket '{bucket_name}' created")
    except Exception as e:
        print(f"❌ S3 bucket creation failed: {e}")
        sys.exit(1)
    
    # Create buildspec for EC2 image
    buildspec = f'''version: 0.2

phases:
  pre_build:
    commands:
      - echo Logging in to Amazon ECR...
      - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com
  build:
    commands:
      - echo "Build started on $(date)"
      - echo "Building the Docker image..."
      - echo "AWS_ACCOUNT_ID=$AWS_ACCOUNT_ID"
      - echo "AWS_DEFAULT_REGION=$AWS_DEFAULT_REGION" 
      - echo "IMAGE_REPO_NAME=$IMAGE_REPO_NAME"
      - echo "IMAGE_TAG=$IMAGE_TAG"
      - echo "Current directory contents:"
      - ls -la
      - echo "Building image with tag:" $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG
      - docker build -t $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG .
  post_build:
    commands:
      - echo "Build completed on $(date)"
      - echo "Pushing the Docker image..."
      - docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME:$IMAGE_TAG
'''
    
    # Create zip file with build context
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
        with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Add Dockerfile
            zip_file.write('ec2-image/Dockerfile', 'Dockerfile')
            # Add requirements.txt
            zip_file.write('ec2-image/requirements.txt', 'requirements.txt')
            # Add buildspec
            zip_file.writestr('buildspec.yml', buildspec)
            # Add .env if exists
            try:
                zip_file.write('.env', '.env')
            except FileNotFoundError:
                print("⚠️  No .env file found")
        
        # Upload source to S3
        s3.upload_file(temp_zip.name, bucket_name, 'source.zip')
        print("✅ Source code uploaded to S3")
        
        # Clean up temp file
        os.unlink(temp_zip.name)
    
    # Create CodeBuild project
    try:
        codebuild.create_project(
            name=project_name,
            source={
                'type': 'S3',
                'location': f"{bucket_name}/source.zip"
            },
            artifacts={'type': 'NO_ARTIFACTS'},
            environment={
                'type': 'LINUX_CONTAINER',
                'image': 'aws/codebuild/standard:7.0',
                'computeType': 'BUILD_GENERAL1_MEDIUM',
                'privilegedMode': True,
                'environmentVariables': [
                    {'name': 'AWS_DEFAULT_REGION', 'value': REGION},
                    {'name': 'AWS_ACCOUNT_ID', 'value': account_id},
                    {'name': 'IMAGE_REPO_NAME', 'value': REPOSITORY_NAME},
                    {'name': 'IMAGE_TAG', 'value': IMAGE_TAG}
                ]
            },
            serviceRole=f"arn:aws:iam::{account_id}:role/codebuild-service-role"
        )
        print(f"✅ CodeBuild project '{project_name}' created")
    except Exception as e:
        print(f"❌ CodeBuild project creation failed: {e}")
        # Try to create the service role and retry
        create_codebuild_service_role()
        print("🔄 Retrying CodeBuild project creation...")
        
        try:
            codebuild.create_project(
                name=project_name,
                source={
                    'type': 'S3',
                    'location': f"{bucket_name}/source.zip"
                },
                artifacts={'type': 'NO_ARTIFACTS'},
                environment={
                    'type': 'LINUX_CONTAINER',
                    'image': 'aws/codebuild/standard:7.0',
                    'computeType': 'BUILD_GENERAL1_MEDIUM',
                    'privilegedMode': True,
                    'environmentVariables': [
                        {'name': 'AWS_DEFAULT_REGION', 'value': REGION},
                        {'name': 'AWS_ACCOUNT_ID', 'value': account_id},
                        {'name': 'IMAGE_REPO_NAME', 'value': REPOSITORY_NAME},
                        {'name': 'IMAGE_TAG', 'value': IMAGE_TAG}
                    ]
                },
                serviceRole=f"arn:aws:iam::{account_id}:role/codebuild-service-role"
            )
            print(f"✅ CodeBuild project '{project_name}' created on retry")
        except Exception as retry_error:
            print(f"❌ CodeBuild project creation failed again: {retry_error}")
            sys.exit(1)
    
    # Start build
    try:
        build_response = codebuild.start_build(projectName=project_name)
        build_id = build_response['build']['id']
        print(f"✅ Build started: {build_id}")
    except Exception as e:
        print(f"❌ Build start failed: {e}")
        sys.exit(1)
    
    # Monitor build
    while True:
        try:
            build_info = codebuild.batch_get_builds(ids=[build_id])
            status = build_info['builds'][0]['buildStatus']
            
            if status == 'SUCCEEDED':
                print("✅ Docker image build completed successfully")
                break
            elif status in ['FAILED', 'FAULT', 'STOPPED', 'TIMED_OUT']:
                print(f"❌ Build failed with status: {status}")
                
                # Get build logs for debugging
                print("🔍 Retrieving build logs...")
                try:
                    logs_client = boto3.client('logs', region_name=REGION)
                    log_group = f"/aws/codebuild/{project_name}"
                    
                    # Get log streams
                    streams = logs_client.describe_log_streams(
                        logGroupName=log_group,
                        orderBy='LastEventTime',
                        descending=True,
                        limit=1
                    )
                    
                    if streams['logStreams']:
                        log_stream = streams['logStreams'][0]['logStreamName']
                        
                        # Get log events
                        events = logs_client.get_log_events(
                            logGroupName=log_group,
                            logStreamName=log_stream
                        )
                        
                        print("📋 Build logs:")
                        for event in events['events']:
                            message = event['message'].strip()
                            if message:
                                print(f"   {message}")
                    else:
                        print("⚠️  No log streams found")
                        
                except Exception as log_error:
                    print(f"⚠️  Could not retrieve logs: {log_error}")
                
                sys.exit(1)
            else:
                print(f"🔄 Build status: {status}")
                time.sleep(30)
        except Exception as e:
            print(f"❌ Build monitoring failed: {e}")
            sys.exit(1)
    
    # Clean up S3 bucket and CodeBuild project
    try:
        s3.delete_object(Bucket=bucket_name, Key='source.zip')
        s3.delete_bucket(Bucket=bucket_name)
        codebuild.delete_project(name=project_name)
        print("✅ Build resources cleaned up")
    except Exception as e:
        print(f"⚠️  Cleanup warning: {e}")

def create_codebuild_service_role():
    """Create CodeBuild service role if it doesn't exist"""
    iam = boto3.client('iam')
    account_id = boto3.client('sts').get_caller_identity()['Account']
    role_name = "codebuild-service-role"
    
    try:
        iam.get_role(RoleName=role_name)
        print(f"✅ CodeBuild service role already exists")
        return
    except iam.exceptions.NoSuchEntityException:
        pass
    
    print(f"🔄 Creating CodeBuild service role...")
    
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "codebuild.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }
        ]
    }
    
    try:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy)
        )
        
        # Attach policies
        policies = [
            "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
            "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser",
            "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"
        ]
        
        for policy in policies:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy)
        
        print(f"✅ CodeBuild service role created")
        time.sleep(10)
        
    except Exception as e:
        print(f"❌ CodeBuild service role creation failed: {e}")

def create_iam_role():
    """Create IAM role for EC2 instance"""
    iam = boto3.client('iam')
    role_name = "ai-executor-ec2-role"
    
    # Check if role exists
    try:
        iam.get_role(RoleName=role_name)
        print(f"✅ IAM role '{role_name}' already exists")
        return role_name
    except iam.exceptions.NoSuchEntityException:
        pass
    
    print(f"🔄 Creating IAM role '{role_name}'...")
    
    # Trust policy for EC2
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }
        ]
    }
    
    try:
        # Create role
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Role for AI Executor EC2 instances"
        )
        
        # Attach policies
        policies = [
            "arn:aws:iam::aws:policy/AmazonEC2ReadOnlyAccess",
            "arn:aws:iam::aws:policy/AmazonS3FullAccess",
            "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
        ]
        
        for policy in policies:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy)
        
        # Create instance profile
        try:
            iam.create_instance_profile(InstanceProfileName=role_name)
            iam.add_role_to_instance_profile(
                InstanceProfileName=role_name,
                RoleName=role_name
            )
        except iam.exceptions.EntityAlreadyExistsException:
            pass
        
        print(f"✅ IAM role '{role_name}' created")
        time.sleep(10)  # Wait for role to propagate
        return role_name
        
    except Exception as e:
        print(f"❌ IAM role creation failed: {e}")
        sys.exit(1)


def upload_files_to_s3(prompt):
    """Upload task prompt, automation script, and scrapers to S3"""
    s3 = boto3.client('s3', region_name=REGION)
    account_id = boto3.client('sts').get_caller_identity()['Account']
    bucket_name = f"ai-executor-results-{account_id}"
    task_key = f"{INSTANCE_NAME}-task.txt"
    script_key = f"{INSTANCE_NAME}-automation_task.py"
    scrapers_key = f"{INSTANCE_NAME}-scrapers.zip"
    
    try:
        # Upload task prompt to S3
        s3.put_object(
            Bucket=bucket_name,
            Key=task_key,
            Body=prompt.encode('utf-8'),
            ContentType='text/plain'
        )
        print(f"✅ Task prompt uploaded to S3: s3://{bucket_name}/{task_key}")
        
        # Upload automation script to S3
        with open('scripts/automation_task.py', 'r') as f:
            automation_script = f.read()
        
        s3.put_object(
            Bucket=bucket_name,
            Key=script_key,
            Body=automation_script.encode('utf-8'),
            ContentType='text/plain'
        )
        print(f"✅ Automation script uploaded to S3: s3://{bucket_name}/{script_key}")
        
        # Create and upload scrapers zip
        import zipfile
        import tempfile
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp_zip:
            with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add all files from scripts/scrapers directory
                scrapers_dir = 'scripts/scrapers'
                for root, dirs, files in os.walk(scrapers_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # Store with relative path starting from scrapers/
                        arcname = os.path.relpath(file_path, 'scripts')
                        zipf.write(file_path, arcname)
            
            # Upload zip to S3
            s3.upload_file(temp_zip.name, bucket_name, scrapers_key)
            print(f"✅ Scrapers uploaded to S3: s3://{bucket_name}/{scrapers_key}")
            
            # Clean up temp file
            os.unlink(temp_zip.name)
        
        return task_key, script_key, scrapers_key
    except Exception as e:
        print(f"❌ Failed to upload files to S3: {e}")
        sys.exit(1)

def launch_ec2_instance(prompt, scraper=None):
    """Launch EC2 instance with user data script"""
    print(f"🔄 Launching EC2 instance '{INSTANCE_NAME}'...")
    
    # Upload task prompt, automation script, and scrapers to S3 first
    task_key, script_key, scrapers_key = upload_files_to_s3(prompt)
    
    ec2 = boto3.client('ec2', region_name=REGION)
    
    # Get default VPC and subnet
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
        if not vpcs['Vpcs']:
            print("❌ No default VPC found")
            sys.exit(1)
        
        vpc_id = vpcs['Vpcs'][0]['VpcId']
        subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
        subnet_id = subnets['Subnets'][0]['SubnetId']
        print(f"✅ Using VPC: {vpc_id}, Subnet: {subnet_id}")
    except Exception as e:
        print(f"❌ VPC/Subnet discovery failed: {e}")
        sys.exit(1)
    
    # Create security group (allow outbound only)
    try:
        sg_response = ec2.create_security_group(
            GroupName=f"{INSTANCE_NAME}-sg",
            Description="AI Executor security group - outbound only",
            VpcId=vpc_id
        )
        security_group_id = sg_response['GroupId']
        print(f"✅ Security group created: {security_group_id}")
    except Exception as e:
        print(f"❌ Security group creation failed: {e}")
        sys.exit(1)
    
    # Launch instance
    try:
        # Read the user data script
        with open('scripts/user_data.sh', 'r') as f:
            user_data = f.read()
        
        # Replace the placeholder with auto-shutdown and S3 download of automation script
        script_replacement = f'''# Schedule automatic shutdown after 3 days (259200 seconds) as safety backup
echo "⏰ Scheduling automatic shutdown in 3 days as safety backup..."
(sleep 259200; echo "🛑 Auto-shutdown timeout reached - terminating instance"; shutdown -h now) &
AUTO_SHUTDOWN_PID=$!
echo "✅ Auto-shutdown scheduled (PID: $AUTO_SHUTDOWN_PID)"

# Download automation script from S3
echo "📥 Downloading automation script from S3..."
aws s3 cp s3://$RESULTS_BUCKET/{script_key} /tmp/automation_task.py --region $REGION
if [ $? -eq 0 ]; then
    echo "✅ Automation script downloaded from S3"
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=automation_script_downloaded" 2>/dev/null || true
else
    echo "❌ Failed to download automation script from S3"
    curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=automation_script_download_failed" 2>/dev/null || true
    echo "⏳ Waiting 30 seconds for final log upload..."
    sleep 30
    shutdown -h now
    exit 1
fi'''
        
        user_data = user_data.replace('# AUTOMATION_SCRIPT_PLACEHOLDER', script_replacement)
        
        print(f"🔍 DEBUG: IMAGE_TAG = '{IMAGE_TAG}'")
        
        response = ec2.run_instances(
            ImageId='ami-0e2c8caa4b6378d8c',  # Ubuntu 24.04 LTS (us-east-1)
            MinCount=1,
            MaxCount=1,
            InstanceType=INSTANCE_TYPE,
            SecurityGroupIds=[security_group_id],
            SubnetId=subnet_id,
            UserData=user_data,
            IamInstanceProfile={'Name': 'ai-executor-ec2-role'},
            InstanceInitiatedShutdownBehavior='terminate',
            MetadataOptions={
                'HttpEndpoint': 'enabled',
                'HttpTokens': 'optional',
                'HttpPutResponseHopLimit': 1,
                'InstanceMetadataTags': 'enabled'
            },
            TagSpecifications=[{
                'ResourceType': 'instance',
                'Tags': [
                    {'Key': 'Name', 'Value': INSTANCE_NAME},
                    {'Key': 'Purpose', 'Value': 'AI-Executor'},
                    {'Key': 'AutoTerminate', 'Value': 'true'},
                    {'Key': 'GOOGLE_API_KEY', 'Value': os.environ.get("GOOGLE_API_KEY", "")},
                    {'Key': 'TASK_ID', 'Value': task_id},
                    {'Key': 'TASK_KEY', 'Value': task_key},
                    {'Key': 'SCRIPT_KEY', 'Value': script_key},
                    {'Key': 'SCRAPERS_KEY', 'Value': scrapers_key},
                    {'Key': 'INSTANCE_NAME', 'Value': INSTANCE_NAME},
                    {'Key': 'IMAGE_TAG', 'Value': IMAGE_TAG},
                    {'Key': 'SCRAPER', 'Value': scraper or ''},
                ]
            }]
        )
        
        instance_id = response['Instances'][0]['InstanceId']
        print(f"✅ EC2 instance launched: {instance_id}")
        return instance_id
        
    except Exception as e:
        print(f"❌ EC2 launch failed: {e}")
        sys.exit(1)

def monitor_instance_and_get_results(instance_id, task_id):
    """Monitor ACTUAL EC2 instance status and console output"""
    print(f"⏳ Monitoring EC2 instance {instance_id} directly...")
    
    ec2 = boto3.client('ec2', region_name=REGION)
    s3 = boto3.client('s3', region_name=REGION)
    
    account_id = boto3.client('sts').get_caller_identity()['Account']
    results_bucket = f"ai-executor-results-{account_id}"
    result_key = f"{task_id}-result.json"
    
    start_time = time.time()
    timeout = 259200  # 3 days timeout (same as auto-shutdown)
    last_console_length = 0
    
    print("📋 EC2 Instance Status:")
    
    while time.time() - start_time < timeout:
        try:
            # Get ACTUAL instance status
            instances = ec2.describe_instances(InstanceIds=[instance_id])
            instance = instances['Reservations'][0]['Instances'][0]
            state = instance['State']['Name']
            
            # Get system status checks
            try:
                status_response = ec2.describe_instance_status(InstanceIds=[instance_id])
                system_status = "initializing"
                instance_status = "initializing"
                
                if status_response['InstanceStatuses']:
                    status = status_response['InstanceStatuses'][0]
                    system_status = status['SystemStatus']['Status']
                    instance_status = status['InstanceStatus']['Status']
                
                print(f"   State: {state} | System: {system_status} | Instance: {instance_status}")
            except:
                print(f"   State: {state} | Status checks not available yet")
            
            # Stream real-time logs from S3
            if state == 'running':
                try:
                    logs_bucket = f"ai-executor-logs-{account_id}"
                    log_key = f"{INSTANCE_NAME}.log"
                    
                    try:
                        response = s3.get_object(Bucket=logs_bucket, Key=log_key)
                        current_log = response['Body'].read().decode()
                        
                        # Only show NEW log content
                        if len(current_log) > last_console_length:
                            new_log = current_log[last_console_length:]
                            if new_log.strip():
                                lines = new_log.strip().split('\n')
                                for line in lines:
                                    if line.strip():
                                        print(f"📋 {line}")
                                        
                                        # Detect browser-use hotlink URL
                                        if "https://cloud.browser-use.com/hotlink?user_code=" in line:
                                            import re
                                            url_match = re.search(r'https://cloud\.browser-use\.com/hotlink\?user_code=[A-Z0-9]+', line)
                                            if url_match:
                                                hotlink_url = url_match.group()
                                                print(f"🔗 Detected browser hotlink: {hotlink_url}")
                                                
                                                # Save hotlink to task JSON
                                                try:
                                                    task_file = f"results/{task_id}.json"
                                                    if os.path.exists(task_file):
                                                        with open(task_file, 'r') as f:
                                                            task_data = json.load(f)
                                                        
                                                        task_data["browser_hotlink"] = hotlink_url
                                                        
                                                        with open(task_file, 'w') as f:
                                                            json.dump(task_data, f, indent=2)
                                                        
                                                        print(f"💾 Browser hotlink saved to task file")
                                                except Exception as save_error:
                                                    print(f"⚠️  Could not save hotlink: {save_error}")
                                        
                            last_console_length = len(current_log)
                            
                    except s3.exceptions.NoSuchKey:
                        # Log file doesn't exist yet - normal during startup
                        pass
                        
                except Exception as e:
                    # Don't spam errors for missing logs during startup
                    if 'NoSuchBucket' not in str(e):
                        print(f"   ⚠️  Log streaming error: {e}")
            
            # Check for results only occasionally to avoid spam
            if state == 'running' and (time.time() - start_time) % 60 < 30:  # Check every other cycle
                try:
                    s3.head_object(Bucket=results_bucket, Key=result_key)
                    print("\n✅ Results found in S3!")
                    
                    # Download and display results
                    response = s3.get_object(Bucket=results_bucket, Key=result_key)
                    result = json.loads(response['Body'].read().decode())
                    
                    print("\n📋 Final Task Results:")
                    print(f"   Status: {result.get('status', 'unknown')}")
                    print(f"   Task: {result.get('task', 'unknown')}")
                    if result.get('result'):
                        print(f"   Result: {result['result']}")
                    if result.get('final_url'):
                        print(f"   Final URL: {result['final_url']}")
                    if result.get('error'):
                        print(f"   Error: {result['error']}")
                    
                    # Save full result to local file
                    result_filename = f"result-{INSTANCE_NAME}.json"
                    with open(result_filename, 'w') as f:
                        json.dump(result, f, indent=2)
                    print(f"💾 Full result saved to: {result_filename}")
                    
                    return result
                    
                except s3.exceptions.NoSuchKey:
                    # No results yet - this is normal
                    pass
                except Exception:
                    # Don't spam S3 errors
                    pass
            
            # Check if instance terminated itself
            if state in ['terminated', 'stopping', 'stopped']:
                print(f"\n🔄 Instance {state} - getting final console output...")
                try:
                    final_console = ec2.get_console_output(InstanceId=instance_id)
                    if 'Output' in final_console:
                        final_lines = final_console['Output'].split('\n')
                        print("📋 Final console output:")
                        for line in final_lines[-30:]:  # Last 30 lines
                            if line.strip():
                                print(f"   {line}")
                except:
                    pass
                
                # ALWAYS try to get results - wait a bit for upload to complete
                print("📥 Checking for final results in S3...")
                for attempt in range(6):  # Try for up to 30 seconds (5s * 6)
                    try:
                        s3.head_object(Bucket=results_bucket, Key=result_key)
                        response = s3.get_object(Bucket=results_bucket, Key=result_key)
                        result = json.loads(response['Body'].read().decode())
                        
                        # Load existing task JSON and add results section
                        os.makedirs("results", exist_ok=True)
                        result_filename = f"results/{task_id}.json"
                        
                        # Load existing task data
                        task_data = {}
                        if os.path.exists(result_filename):
                            with open(result_filename, 'r') as f:
                                task_data = json.load(f)
                        
                        # Add the automation results
                        task_data["automation_result"] = result
                        
                        # Save updated task data
                        with open(result_filename, 'w') as f:
                            json.dump(task_data, f, indent=2)
                        
                        print("✅ Found result in S3!")
                        print(f"💾 Result saved to: {result_filename}")
                        print(f"📋 Full result:")
                        print(json.dumps(result, indent=2))
                        
                        return result
                    except s3.exceptions.NoSuchKey:
                        if attempt < 5:  # Not the last attempt
                            print(f"   Attempt {attempt + 1}/6 - waiting for result upload...")
                            time.sleep(5)
                        else:
                            print("❌ No result found in S3 after 30 seconds")
                            return {"status": "error", "error": "Instance terminated without uploading results"}
                    except Exception as e:
                        print(f"❌ Error checking S3 results: {e}")
                        return {"status": "error", "error": f"S3 error: {str(e)}"}
            
            print("-" * 40)
            time.sleep(30)  # Check every 30 seconds
            
        except Exception as e:
            print(f"⚠️  Error during monitoring: {e}")
            time.sleep(10)
    
    print("\n⏰ Timeout reached - task may still be running")
    return {"status": "timeout", "error": "Task timed out after 3 days"}


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Deploy EC2 instance for browser automation')
    parser.add_argument('--task', required=True, help='The automation task to perform')
    parser.add_argument('--scraper', help='Run scraper after automation task (optional)')
    parser.add_argument('--task-id', required=True, help='UUID for task tracking')
    
    args = parser.parse_args()
    
    # Append Cloudflare verification instructions to the task prompt
    cloudflare_instructions = "\n\nIn case you see a verification checkbox, always wait 10 seconds for the verification checkbox to appear. Once it appears, click it once, and wait 5 more seconds."
    prompt = args.task + cloudflare_instructions
    scraper = args.scraper
    task_id = args.task_id
    
    print(f"🎯 Task ID: {task_id}")
    print(f"🎯 Task: {prompt}")
    print(f"🏷️  Instance: {INSTANCE_NAME}")
    if scraper:
        print(f"🔧 Scraper: {scraper}")
    
    check_aws_credentials()
    build_docker_image_if_needed()
    role_name = create_iam_role()
    instance_id = launch_ec2_instance(prompt, scraper)
    
    print("\n🚀 EC2 deployment completed!")
    print(f"📋 Instance ID: {instance_id}")
    print("⏳ Instance will auto-terminate when task completes")
    print("📊 Monitoring for results...")
    
    result = monitor_instance_and_get_results(instance_id, task_id)
    
    print("\n🎉 Task completed!")

if __name__ == "__main__":
    main()