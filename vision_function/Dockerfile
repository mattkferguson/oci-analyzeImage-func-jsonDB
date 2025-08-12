# Use Oracle Linux 9 for consistency with web app
FROM --platform=linux/amd64 oraclelinux:9

# Install the Oracle Instant Client 23ai repo, then the client and Python 3
RUN dnf -y install oracle-instantclient-release-23ai-el9 && \
    dnf -y install oracle-instantclient-basic python3 python3-pip && \
    dnf clean all

# Set the working directory
WORKDIR /function

# Set Python path
ENV PYTHONPATH=/python

# Copy and install dependencies
COPY vision_function/requirements.txt /function/requirements.txt
RUN python3 -m pip install --target /python --no-cache-dir -r /function/requirements.txt

# Copy function code
COPY vision_function/func.py /function/func.py

# Copy wallet files for database authentication
COPY ./config/* /function/wallet/

# Set proper permissions on wallet files and fix sqlnet.ora for container environment
# Use more permissive permissions for Functions environment  
RUN chmod -R 644 /function/wallet/*.p12 /function/wallet/*.sso /function/wallet/*.pem /function/wallet/*.jks 2>/dev/null || true && \
    chmod 644 /function/wallet/tnsnames.ora /function/wallet/sqlnet.ora /function/wallet/ojdbc.properties 2>/dev/null || true && \
    chown -R root:root /function/wallet && \
    echo 'WALLET_LOCATION = (SOURCE = (METHOD = file) (METHOD_DATA = (DIRECTORY="/function/wallet")))' > /function/wallet/sqlnet.ora && \
    echo 'SSL_SERVER_DN_MATCH=yes' >> /function/wallet/sqlnet.ora

ENV TNS_ADMIN=/function/wallet
ENV ORACLE_HOME=/usr/lib/oracle/23/client64

# Set entrypoint
ENTRYPOINT ["/python/bin/fdk", "/function/func.py", "handler"]