# Use Python 3.11 slim image for REST API version
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Copy the application code  
COPY ./app/app.py /app/app.py
COPY ./app/templates /app/templates

# Uses Resource Principal authentication - no wallet files needed

# Copy and install REST version requirements
COPY ./app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port 5000 available
EXPOSE 5000

# Run the application using Python
CMD ["python", "app.py"]