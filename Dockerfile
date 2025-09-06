# Use AWS Lambda Python runtime as base
FROM public.ecr.aws/lambda/python:3.11

# Set environment variables to suppress interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Update and install system packages
RUN yum update -y && yum install -y \
    wget \
    curl \
    unzip \
    fontconfig \
    libX11 \
    libXcomposite \
    libXcursor \
    libXdamage \
    libXext \
    libXi \
    libXrandr \
    libXrender \
    libXss \
    libXtst \
    libXScrnSaver \
    libdrm \
    libxkbcommon \
    libatspi \
    cups-libs \
    dbus-glib \
    gtk3 \
    libdrm \
    libxkbcommon \
    mesa-libgbm

# Install Node.js (needed for Playwright)
RUN curl -fsSL https://rpm.nodesource.com/setup_18.x | bash - && \
    yum install -y nodejs

# Copy requirements and install Python packages
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install -r requirements.txt

# Install Playwright and browsers
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy Lambda function
COPY lambda_function.py ${LAMBDA_TASK_ROOT}/

# Set the CMD to your handler
CMD ["lambda_function.lambda_handler"]