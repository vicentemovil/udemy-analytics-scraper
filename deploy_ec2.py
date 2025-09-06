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


def launch_ec2_instance(prompt):
    """Launch EC2 instance with user data script"""
    print(f"🔄 Launching EC2 instance '{INSTANCE_NAME}'...")
    
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
        # Read the user data script and automation script
        with open('scripts/user_data.sh', 'r') as f:
            user_data = f.read()
        
        with open('scripts/automation_task.py', 'r') as f:
            automation_script = f.read()
        
        # Replace the placeholder with the actual automation script
        script_replacement = f'''# Create automation script on EC2 instance
curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=creating_automation_script" 2>/dev/null || true
cat > /tmp/automation_task.py << 'SCRIPT_EOF'
{automation_script}
SCRIPT_EOF
curl -X POST http://requestbin.whapi.cloud/1phw2m41 -d "status=automation_script_created" 2>/dev/null || true
log "✅ Automation script created"'''
        
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
            TagSpecifications=[{
                'ResourceType': 'instance',
                'Tags': [
                    {'Key': 'Name', 'Value': INSTANCE_NAME},
                    {'Key': 'Purpose', 'Value': 'AI-Executor'},
                    {'Key': 'AutoTerminate', 'Value': 'true'},
                    {'Key': 'GOOGLE_API_KEY', 'Value': os.environ.get("GOOGLE_API_KEY", "")},
                    {'Key': 'TASK_PROMPT', 'Value': prompt},
                    {'Key': 'INSTANCE_NAME', 'Value': INSTANCE_NAME},
                    {'Key': 'IMAGE_TAG', 'Value': IMAGE_TAG}
                ]
            }]
        )
        
        instance_id = response['Instances'][0]['InstanceId']
        print(f"✅ EC2 instance launched: {instance_id}")
        return instance_id
        
    except Exception as e:
        print(f"❌ EC2 launch failed: {e}")
        sys.exit(1)

def monitor_instance_and_get_results(instance_id):
    """Monitor ACTUAL EC2 instance status and console output"""
    print(f"⏳ Monitoring EC2 instance {instance_id} directly...")
    
    ec2 = boto3.client('ec2', region_name=REGION)
    s3 = boto3.client('s3', region_name=REGION)
    
    account_id = boto3.client('sts').get_caller_identity()['Account']
    results_bucket = f"ai-executor-results-{account_id}"
    result_key = f"{INSTANCE_NAME}-result.json"
    
    start_time = time.time()
    timeout = 1800  # 30 minutes timeout
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
                    if result.get('error'):
                        print(f"   Error: {result['error']}")
                    
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
                
                # Check for final results
                try:
                    s3.head_object(Bucket=results_bucket, Key=result_key)
                    response = s3.get_object(Bucket=results_bucket, Key=result_key)
                    result = json.loads(response['Body'].read().decode())
                    return result
                except:
                    return {"status": "error", "error": "Instance terminated without results"}
            
            print("-" * 40)
            time.sleep(30)  # Check every 30 seconds
            
        except Exception as e:
            print(f"⚠️  Error during monitoring: {e}")
            time.sleep(10)
    
    print("\n⏰ Timeout reached - task may still be running")
    return {"status": "timeout", "error": "Task timed out after 30 minutes"}

def main():
    if len(sys.argv) < 2:
        print("Usage: python deploy_ec2.py 'your prompt here'")
        print("Example: python deploy_ec2.py 'Navigate to Amazon and find the current price of iPhone 15'")
        sys.exit(1)
    
    prompt = sys.argv[1]
    print(f"🎯 Task: {prompt}")
    print(f"🏷️  Instance: {INSTANCE_NAME}")
    
    check_aws_credentials()
    build_docker_image_if_needed()
    role_name = create_iam_role()
    instance_id = launch_ec2_instance(prompt)
    
    print("\n🚀 EC2 deployment completed!")
    print(f"📋 Instance ID: {instance_id}")
    print("⏳ Instance will auto-terminate when task completes")
    print("📊 Monitoring for results...")
    
    result = monitor_instance_and_get_results(instance_id)
    
    print("\n🎉 Task completed!")

if __name__ == "__main__":
    main()