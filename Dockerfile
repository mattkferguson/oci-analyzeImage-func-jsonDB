# Use Oracle Linux 9 as the base image, per user suggestion.
# Force x86_64 platform for Oracle Instant Client compatibility
FROM --platform=linux/amd64 oraclelinux:9

# Install the Oracle Instant Client 23ai repo, then the client and Python 3
RUN dnf -y install oracle-instantclient-release-23ai-el9 && \
    dnf -y install oracle-instantclient-basic python3 python3-pip && \
    dnf clean all

# Set the working directory
WORKDIR /app

# Copy the application code
COPY ./app /app

# Copy wallet files for database authentication
COPY ./config/* /app/wallet/

# Set proper permissions on wallet files and fix sqlnet.ora for container environment
RUN chmod -R 600 /app/wallet/*.p12 /app/wallet/*.sso /app/wallet/*.pem 2>/dev/null || true && \
    chmod 644 /app/wallet/tnsnames.ora /app/wallet/sqlnet.ora /app/wallet/ojdbc.properties 2>/dev/null || true && \
    chown -R root:root /app/wallet && \
    echo 'WALLET_LOCATION = (SOURCE = (METHOD = file) (METHOD_DATA = (DIRECTORY="/app/wallet")))' > /app/wallet/sqlnet.ora && \
    echo 'SSL_SERVER_DN_MATCH=yes' >> /app/wallet/sqlnet.ora

ENV TNS_ADMIN=/app/wallet
ENV ORACLE_HOME=/usr/lib/oracle/23/client64

# Install Python dependencies using pip for Python 3
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# Make port 5000 available
EXPOSE 5000

# Run the application using Python 3
CMD ["python3", "app.py"]