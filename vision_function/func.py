import io
import json
import logging
import oci
import oracledb
import os
import sys
import traceback
from fdk import response
from cryptography.hazmat.primitives import serialization

# --- Thick Mode for SODA Support ---
# Initialize thick mode for SODA compatibility with Oracle 23ai
try:
    # Initialize thick mode with Oracle Instant Client
    oracledb.init_oracle_client()
    logging.getLogger().info("Successfully initialized oracledb thick mode for SODA support")
except Exception as e:
    logging.getLogger().error(f"Failed to initialize thick mode: {e}")
    logging.getLogger().info("Note: SODA operations may not be available")

# Database Configuration
COLLECTION_NAME = "vision_results"

def get_db_connection(cfg, signer):
    """Establishes a connection to the Oracle Database using wallet files or Resource Principals."""
    log = logging.getLogger()
    wallet_location = "/function/wallet"
    
    # First try with actual wallet files
    if os.path.exists(wallet_location):
        log.info(f"Found wallet files at {wallet_location}")
        
        # Debug: List all files in wallet directory
        try:
            wallet_files = os.listdir(wallet_location)
            log.info(f"Wallet directory contains: {wallet_files}")
            
            # Check specific wallet files
            for wallet_file in ['cwallet.sso', 'ewallet.p12', 'sqlnet.ora', 'tnsnames.ora']:
                file_path = os.path.join(wallet_location, wallet_file)
                if os.path.exists(file_path):
                    stat_info = os.stat(file_path)
                    log.info(f"{wallet_file}: exists, size={stat_info.st_size}, mode={oct(stat_info.st_mode)}")
                else:
                    log.info(f"{wallet_file}: NOT FOUND")
        except Exception as e:
            log.error(f"Error listing wallet files: {e}")
        
        # Check environment variables
        log.info(f"TNS_ADMIN={os.environ.get('TNS_ADMIN', 'NOT SET')}")
        log.info(f"ORACLE_HOME={os.environ.get('ORACLE_HOME', 'NOT SET')}")
        
        # Check for database password environment variable
        db_password = cfg.get("DB_PASSWORD", "")
        
        # Debug: Check if we can actually read wallet file contents
        try:
            cwallet_path = os.path.join(wallet_location, "cwallet.sso")
            with open(cwallet_path, 'rb') as f:
                first_bytes = f.read(10)
                log.info(f"Successfully read first 10 bytes from cwallet.sso: {first_bytes.hex()}")
        except Exception as e:
            log.error(f"Failed to read cwallet.sso file: {e}")
            
        try:
            sqlnet_path = os.path.join(wallet_location, "sqlnet.ora")
            with open(sqlnet_path, 'r') as f:
                sqlnet_content = f.read()
                log.info(f"sqlnet.ora content: {sqlnet_content}")
        except Exception as e:
            log.error(f"Failed to read sqlnet.ora file: {e}")
        
        connection_attempts = [
            ("Auto-login wallet authentication", lambda: oracledb.connect(
                dsn="visionjsondb_medium"  # TNS_ADMIN env var points to wallet location
            )),
            ("Wallet with ADMIN user and password", lambda: oracledb.connect(
                user="ADMIN",
                password=db_password,
                dsn="visionjsondb_medium",
                wallet_location=wallet_location,
                wallet_password=""
            )),
            ("Wallet authentication with location", lambda: oracledb.connect(
                dsn="visionjsondb_medium",
                wallet_location=wallet_location,
                wallet_password=""
            )),
            ("Wallet authentication with config_dir", lambda: oracledb.connect(
                dsn="visionjsondb_medium",
                config_dir=wallet_location,
                wallet_location=wallet_location,
                wallet_password=""
            )),
            ("Wallet with explicit ADMIN user (no password)", lambda: oracledb.connect(
                user="ADMIN",
                dsn="visionjsondb_medium",
                wallet_location=wallet_location,
                wallet_password=""
            ))
        ]
    else:
        # Fallback to Resource Principals approach
        dsn = cfg["DB_CONNECTION_STRING"]
        log.info(f"No wallet found, trying Resource Principals with DSN: {dsn}")
        connection_attempts = [
            ("Resource Principals wallet-based", lambda: oracledb.connect(
                dsn=dsn,
                config_dir="/dev/null",
                wallet_location="/dev/null", 
                wallet_password=""
            )),
            ("Basic connection with Resource Principals", lambda: oracledb.connect(dsn=dsn))
        ]
    
    # Try all connection attempts
    for attempt_name, connection_func in connection_attempts:
        try:
            log.info(f"Trying {attempt_name}...")
            connection = connection_func()
            log.info(f"Successfully connected using {attempt_name}")
            return connection
        except Exception as e:
            log.info(f"{attempt_name} failed: {e}")
            continue
    
    # If all attempts failed, raise an exception
    raise Exception("All database connection attempts failed")

def handler(ctx, data: io.BytesIO=None):
    log = logging.getLogger()
    
    try:
        log.info("Function execution started.")
        cfg = ctx.Config()
        
        signer = oci.auth.signers.get_resource_principals_signer()
        vision_client = oci.ai_vision.AIServiceVisionClient(config={}, signer=signer)
        log.info("Successfully initialized OCI Vision client.")

        event_data = data.getvalue()
        if not event_data:
            raise ValueError("Input data from FDK was empty.")

        body = json.loads(event_data)
        log.info("Successfully parsed event data.")

        source_bucket = body["data"]["additionalDetails"]["bucketName"]
        source_object = body["data"]["resourceName"]
        namespace = body["data"]["additionalDetails"]["namespace"]
        compartment_id = body["data"]["compartmentId"]
        
        log.info(f"Processing {source_object} from bucket {source_bucket}.")

        image_details = oci.ai_vision.models.ObjectStorageImageDetails(
            namespace_name=namespace,
            bucket_name=source_bucket,
            object_name=source_object
        )
        
        analyze_image_details = oci.ai_vision.models.AnalyzeImageDetails(
            features=[oci.ai_vision.models.ImageObjectDetectionFeature()],
            image=image_details,
            compartment_id=compartment_id
        )

        log.info("Calling Vision API...")
        vision_response = vision_client.analyze_image(analyze_image_details)
        result_dict = oci.util.to_dict(vision_response.data)
        log.info(f"Successfully analyzed {source_object}.")

        result_dict['image_name'] = source_object

        log.info("Connecting to the Autonomous JSON Database...")
        with get_db_connection(cfg, signer) as connection:
            soda = connection.getSodaDatabase()
            
            # Try to open existing collection first
            collection = None
            try:
                collection = soda.openCollection(COLLECTION_NAME)
                if collection:
                    log.info(f"Using existing SODA collection: {COLLECTION_NAME}")
            except Exception as e:
                log.info(f"Could not open existing collection: {e}")
            
            # Collection doesn't exist or failed to open, create new one
            if not collection:
                try:
                    # Create collection with string keys for Oracle 23ai compatibility
                    collection_metadata = {
                        "keyColumn": {
                            "name": "ID",
                            "sqlType": "VARCHAR2",
                            "maxLength": 255,
                            "assignmentMethod": "CLIENT"
                        }
                    }
                    collection = soda.createCollection(COLLECTION_NAME, collection_metadata)
                    log.info(f"Created new SODA collection: {COLLECTION_NAME}")
                except Exception as create_error:
                    # If creation fails due to existing collection with different metadata,
                    # try to drop and recreate
                    if "ORA-40669" in str(create_error):
                        try:
                            log.info(f"Collection exists with different metadata, dropping and recreating...")
                            existing_collection = soda.openCollection(COLLECTION_NAME)
                            if existing_collection:
                                existing_collection.drop()
                            # Now create with new metadata
                            collection = soda.createCollection(COLLECTION_NAME, collection_metadata)
                            log.info(f"Successfully recreated SODA collection: {COLLECTION_NAME}")
                        except Exception as drop_error:
                            log.error(f"Failed to drop and recreate collection: {drop_error}")
                            raise drop_error
                    else:
                        raise create_error
            
            log.info(f"Inserting analysis for {source_object} into the database.")
            
            # Add filename to the document content for easier querying
            result_dict['filename'] = source_object
            
            try:
                # Try simple insertOne first
                result = collection.insertOne(result_dict)
                log.info(f"insertOne result type: {type(result)}")
                log.info(f"insertOne result: {result}")
                
                # Try to get document count to verify insertion
                doc_count = collection.find().count()
                log.info(f"Total documents in collection after insert: {doc_count}")
                
                # Explicitly commit the transaction
                connection.commit()
                log.info("Successfully saved analysis to the database and committed transaction.")
                
            except Exception as e:
                log.error(f"Error inserting document: {e}")
                # Try alternative approach - insertOneAndGet
                try:
                    result = collection.insertOneAndGet(result_dict)
                    log.info(f"insertOneAndGet result: {result}")
                    if result and hasattr(result, 'key'):
                        log.info(f"Successfully saved analysis with key: {result.key}")
                    else:
                        log.info("insertOneAndGet succeeded but no key returned")
                    
                    # Commit the fallback transaction too
                    connection.commit()
                    log.info("Committed fallback transaction.")
                except Exception as e2:
                    log.error(f"insertOneAndGet also failed: {e2}")
                    raise e2

        return response.Response(
            ctx, 
            response_data=json.dumps({"status": "Success"}),
            headers={"Content-Type": "application/json"}
        )

    except Exception as e:
        error_message = f"Top-level error in function handler: {str(e)}"
        log.error(error_message)
        print(error_message, file=sys.stderr)
        return response.Response(
            ctx, 
            response_data=json.dumps({"status": "Error", "message": error_message}),
            status_code=500,
            headers={"Content-Type": "application/json"}
        )
