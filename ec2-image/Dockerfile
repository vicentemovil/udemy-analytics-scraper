# EC2 base runtime image with browsers explicitly installed
FROM public.ecr.aws/ubuntu/ubuntu:24.04

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies and Python
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager (required for re-browser-use)
RUN pip3 install --break-system-packages uv

# Install re-browser-use using uv with --break-system-packages equivalent
RUN uv pip install --system --break-system-packages re-browser-use

# Install Chromium with dependencies using uvx (as per their README)
RUN uvx playwright install chromium --with-deps --no-shell

# Install Xvfb for virtual display support (needed for headless=False)
RUN apt-get update && apt-get install -y xvfb && rm -rf /var/lib/apt/lists/*

# Install AWS CLI for results upload
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm -rf awscliv2.zip aws

# Create working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/
RUN pip3 install --break-system-packages -r requirements.txt

# Create directories for browser data
RUN mkdir -p /app/chrome-user-data /app/downloads

# Set up virtual display environment for headless=False
ENV DISPLAY=:99

# Default command (will be overridden by EC2 user-data)
CMD ["sh", "-c", "Xvfb :99 -screen 0 1920x1080x24 & python3 -c \"print('EC2 runtime ready - Chromium with virtual display')\""]