# EC2 base runtime image with browsers explicitly installed
FROM ubuntu:24.04

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

# Install Playwright and browsers (override PEP 668 protection)
RUN pip3 install --break-system-packages playwright
RUN playwright install chromium
RUN playwright install-deps chromium

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

# Default command (will be overridden by EC2 user-data)
CMD ["python3", "-c", "print('EC2 runtime ready - Chromium installed')"]