# Use Python 3.11 slim image for REST API version
FROM python:3.11-slim

# Set the working directory
WORKDIR /function

# Set Python path
ENV PYTHONPATH=/python

# Copy and install dependencies
COPY vision_function/requirements.txt /function/requirements.txt
RUN pip install --target /python --no-cache-dir -r /function/requirements.txt

# Copy function code
COPY vision_function/func.py /function/func.py

# Uses Resource Principal authentication - no wallet files needed

# Set entrypoint
ENTRYPOINT ["/python/bin/fdk", "/function/func.py", "handler"]