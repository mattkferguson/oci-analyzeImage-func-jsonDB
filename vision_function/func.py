import base64
import io
import json
import logging
import oci
import os
import requests
import traceback
from datetime import datetime
from fdk import response

# Database REST API Configuration (defaults; can be overridden via Vault/env)
DB_ORDS_BASE_URL = os.environ.get(
    "DB_ORDS_BASE_URL",
    "https://g4f1b0a16e960d1-visionjsondb.adb.ca-toronto-1.oraclecloudapps.com/ords/"
)
DB_SCHEMA = "admin"  # Schema name (lowercase for URL)
DB_SODA_PATH = f"{DB_SCHEMA}/soda/latest"
DB_BASE_URL = f"{DB_ORDS_BASE_URL}{DB_SODA_PATH}"
DB_COLLECTION = "IMAGE_ANALYSIS"
DB_USERNAME = os.environ.get("DB_USERNAME")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
secrets_client = None
signer = None

def _fetch_secret_from_vault(secret_ocid):
    """Fetch and decode a secret value from OCI Vault using Resource Principals.
    Returns decoded UTF-8 string or None on failure.
    """
    global secrets_client, signer
    try:
        if not signer:
            signer = oci.auth.signers.get_resource_principals_signer()
        if not secrets_client:
            secrets_client = oci.secrets.SecretsClient(config={"region": signer.region}, signer=signer)
        resp = secrets_client.get_secret_bundle(secret_id=secret_ocid)
        content_b64 = resp.data.secret_bundle_content.content
        return base64.b64decode(content_b64).decode("utf-8")
    except Exception as e:
        logging.getLogger().error(f"Failed to fetch secret {secret_ocid} from Vault: {e}")
        return None

def load_db_config_from_vault_if_available():
    """Load DB_ORDS_BASE_URL, DB_USERNAME, DB_PASSWORD from OCI Vault if OCIDs are provided.
    Updates globals and recomputes DB_BASE_URL.
    """
    global DB_ORDS_BASE_URL, DB_USERNAME, DB_PASSWORD, DB_BASE_URL
    pw_secret_id = os.environ.get("DB_PASSWORD_SECRET_OCID")
    user_secret_id = os.environ.get("DB_USERNAME_SECRET_OCID")
    ords_url_secret_id = os.environ.get("DB_ORDS_URL_SECRET_OCID")

    if not any([pw_secret_id, user_secret_id, ords_url_secret_id]):
        return

    log = logging.getLogger()
    log.info("Attempting to load DB config from OCI Vault via Resource Principals...")
    if user_secret_id:
        v = _fetch_secret_from_vault(user_secret_id)
        if v:
            DB_USERNAME = v.strip()
            log.info("Loaded DB_USERNAME from Vault.")
    if pw_secret_id:
        v = _fetch_secret_from_vault(pw_secret_id)
        if v:
            DB_PASSWORD = v
            log.info("Loaded DB_PASSWORD from Vault.")
    if ords_url_secret_id:
        v = _fetch_secret_from_vault(ords_url_secret_id)
        if v:
            DB_ORDS_BASE_URL = v.strip()
            log.info("Loaded DB_ORDS_BASE_URL from Vault.")
    DB_BASE_URL = f"{DB_ORDS_BASE_URL}{DB_SODA_PATH}"

def ensure_collection_exists(db_password):
    """Ensure the SODA collection exists, create if it doesn't."""
    log = logging.getLogger()
    
    try:
        auth = (DB_USERNAME, db_password)
        headers = {'Content-Type': 'application/json'}
        
        # Check if collection exists by trying to get it
        response = requests.get(
            f"{DB_BASE_URL}/{DB_COLLECTION}",
            auth=auth,
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            log.info(f"Collection {DB_COLLECTION} already exists")
            return True
        elif response.status_code == 404:
            # Collection doesn't exist, create it
            log.info(f"Collection {DB_COLLECTION} not found, creating...")
            
            # Create collection with metadata
            collection_metadata = {
                "schemaName": "ADMIN",
                "tableName": DB_COLLECTION,
                "keyColumn": {
                    "name": "ID",
                    "sqlType": "VARCHAR2",
                    "maxLength": 255,
                    "assignmentMethod": "UUID"
                },
                "contentColumn": {
                    "name": "JSON_DOCUMENT",
                    "sqlType": "BLOB",
                    "jsonFormat": "OSON"
                },
                "versionColumn": {
                    "name": "VERSION",
                    "method": "UUID"
                },
                "lastModifiedColumn": {
                    "name": "LAST_MODIFIED"
                },
                "creationTimeColumn": {
                    "name": "CREATED_ON"
                }
            }
            
            create_response = requests.put(
                f"{DB_BASE_URL}/{DB_COLLECTION}",
                auth=auth,
                headers=headers,
                json=collection_metadata,
                timeout=30
            )
            
            if create_response.status_code in [200, 201]:
                log.info(f"Successfully created collection {DB_COLLECTION}")
                return True
            else:
                log.error(f"Failed to create collection: HTTP {create_response.status_code}")
                log.error(f"Response: {create_response.text}")
                return False
        else:
            log.error(f"Unexpected response checking collection: HTTP {response.status_code}")
            try:
                log.error(f"Response body: {response.text}")
            except Exception:
                pass
            return False
            
    except Exception as e:
        log.error(f"Error ensuring collection exists: {e}")
        return False

def store_analysis_result_via_rest(image_name, bucket_name, analysis_results, db_password):
    """Store image analysis results in database via REST API."""
    log = logging.getLogger()
    
    try:
        # Ensure collection exists before storing data
        if not ensure_collection_exists(db_password):
            log.error("Failed to ensure collection exists")
            return False
        
        # Prepare the document to store
        document = {
            "image_name": image_name,
            "bucket_name": bucket_name,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "analysis_results": analysis_results
        }
        
        # REST API call to insert document
        auth = (DB_USERNAME, db_password)
        headers = {'Content-Type': 'application/json'}
        
        response_req = requests.post(
            f"{DB_BASE_URL}/{DB_COLLECTION}",
            auth=auth,
            headers=headers,
            json=document,
            timeout=30
        )
        
        if response_req.status_code == 201:
            result = response_req.json()
            doc_id = result.get('id')
            log.info(f"Successfully stored analysis result with ID: {doc_id}")
            return True
        else:
            log.error(f"Failed to store analysis result: HTTP {response_req.status_code}")
            log.error(f"Response: {response_req.text}")
            return False
            
    except Exception as e:
        log.error(f"Error storing analysis result via REST: {e}")
        log.error(traceback.format_exc())
        return False

def handler(ctx, data: io.BytesIO = None):
    """
    OCI Function handler for processing Object Storage events and running AI Vision analysis.
    """
    log = logging.getLogger()
    log.info("Function invoked")
    
    try:
        # Parse the event data
        body = json.loads(data.getvalue())
        log.info(f"Event received: {json.dumps(body, indent=2)}")
        
        # Extract event information
        event_type = body.get("eventType", "")
        if event_type != "com.oraclecloud.objectstorage.createobject":
            log.info(f"Ignoring event type: {event_type}")
            return response.Response(
                ctx, 
                response_data=json.dumps({"message": "Event ignored"}),
                headers={"Content-Type": "application/json"}
            )
        
        # Extract object information from event structure
        data_info = body.get("data", {})
        additional_details = data_info.get("additionalDetails", {})
        
        # Debug: Log the full event structure to understand the format
        log.info(f"Full event body keys: {list(body.keys())}")
        log.info(f"data keys: {list(data_info.keys())}")
        log.info(f"additionalDetails keys: {list(additional_details.keys())}")
        
        # Try multiple possible locations for object name
        object_name = ""
        possible_names = [
            additional_details.get("objectName", ""),
            data_info.get("objectName", ""),
            data_info.get("resourceName", ""),
            body.get("resourceName", ""),
            body.get("objectName", "")
        ]
        
        # Also try parsing from resourceId if it exists
        resource_id = data_info.get("resourceId", "") or body.get("resourceId", "")
        if resource_id and not any(possible_names):
            # resourceId format: /n/namespace/b/bucket/o/objectname
            parts = resource_id.split("/")
            if len(parts) >= 6 and parts[4] == "o":
                possible_names.append(parts[5])
        
        # Find the first non-empty name
        for name in possible_names:
            if name and name.strip():
                object_name = name.strip()
                break
        
        bucket_name = additional_details.get("bucketName", "")
        namespace = additional_details.get("namespace", "")
        
        log.info(f"Extracted - Object: '{object_name}', Bucket: '{bucket_name}', Namespace: '{namespace}'")
        log.info(f"Resource ID: '{resource_id}'")
        
        if not object_name or not bucket_name:
            log.error(f"Missing object name ('{object_name}') or bucket name ('{bucket_name}') in event")
            log.error(f"Tried these name sources: {possible_names}")
            return response.Response(
                ctx,
                response_data=json.dumps({"error": "Missing object information"}),
                headers={"Content-Type": "application/json"},
                status_code=400
            )
        
        # Load database credentials from Vault if available
        load_db_config_from_vault_if_available()
        db_password = DB_PASSWORD
        db_username = DB_USERNAME
        if not db_username or not db_password:
            log.error("Database credentials not configured (Vault/env)")
            return response.Response(
                ctx,
                response_data=json.dumps({"error": "Database credentials not configured"}),
                headers={"Content-Type": "application/json"},
                status_code=500
            )
        
        # Initialize OCI clients using Resource Principals
        log.info("Initializing OCI clients...")
        try:
            signer = oci.auth.signers.get_resource_principals_signer()
            config = {'region': signer.region}
            
            # Initialize Vision client
            vision_client = oci.ai_vision.AIServiceVisionClient(config=config, signer=signer)
            log.info("Vision client initialized")
            
            # Initialize Object Storage client
            object_storage_client = oci.object_storage.ObjectStorageClient(config=config, signer=signer)
            log.info("Object Storage client initialized")
            
        except Exception as e:
            log.error(f"Failed to initialize OCI clients: {e}")
            return response.Response(
                ctx,
                response_data=json.dumps({"error": "Failed to initialize OCI clients"}),
                headers={"Content-Type": "application/json"},
                status_code=500
            )
        
        # Note: We don't need to fetch the image data since we're using Object Storage reference
        log.info(f"Will analyze object {object_name} from bucket {bucket_name} via Object Storage reference")
        
        # Perform image analysis
        try:
            log.info("Starting image analysis...")
            
            # Create image object detection request using Object Storage reference
            object_storage_image_details = oci.ai_vision.models.ObjectStorageImageDetails(
                source="OBJECT_STORAGE",
                namespace_name=namespace,
                bucket_name=bucket_name,
                object_name=object_name
            )
            
            image_object_detection_feature = oci.ai_vision.models.ImageObjectDetectionFeature(
                feature_type="OBJECT_DETECTION",
                max_results=10
            )
            
            # Analyze image features  
            analyze_image_details = oci.ai_vision.models.AnalyzeImageDetails(
                features=[image_object_detection_feature],
                image=object_storage_image_details,
                compartment_id=os.environ.get("TENANCY_OCID", "")
            )
            
            # Call Vision API
            analyze_image_response = vision_client.analyze_image(analyze_image_details=analyze_image_details)
            
            # Process results
            image_objects = analyze_image_response.data.image_objects
            
            analysis_results = {
                "objects": []
            }
            
            for obj in image_objects:
                obj_data = {
                    "name": obj.name,
                    "confidence": float(obj.confidence),
                    "bounding_box": {
                        "left": obj.bounding_polygon.normalized_vertices[0].x,
                        "top": obj.bounding_polygon.normalized_vertices[0].y,
                        "width": abs(obj.bounding_polygon.normalized_vertices[2].x - obj.bounding_polygon.normalized_vertices[0].x),
                        "height": abs(obj.bounding_polygon.normalized_vertices[2].y - obj.bounding_polygon.normalized_vertices[0].y)
                    }
                }
                analysis_results["objects"].append(obj_data)
            
            log.info(f"Analysis completed, found {len(analysis_results['objects'])} objects")
            
        except Exception as e:
            log.error(f"Failed to analyze image: {e}")
            log.error(traceback.format_exc())
            return response.Response(
                ctx,
                response_data=json.dumps({"error": "Failed to analyze image"}),
                headers={"Content-Type": "application/json"},
                status_code=500
            )
        
        # Store results in database via REST API
        try:
            log.info("Storing analysis results in database...")
            success = store_analysis_result_via_rest(object_name, bucket_name, analysis_results, db_password)
            
            if success:
                log.info("Analysis results stored successfully")
                return response.Response(
                    ctx,
                    response_data=json.dumps({
                        "message": "Image analysis completed successfully",
                        "image_name": object_name,
                        "bucket_name": bucket_name,
                        "objects_found": len(analysis_results["objects"])
                    }),
                    headers={"Content-Type": "application/json"}
                )
            else:
                log.error("Failed to store analysis results")
                return response.Response(
                    ctx,
                    response_data=json.dumps({"error": "Failed to store analysis results"}),
                    headers={"Content-Type": "application/json"},
                    status_code=500
                )
                
        except Exception as e:
            log.error(f"Error storing results: {e}")
            log.error(traceback.format_exc())
            return response.Response(
                ctx,
                response_data=json.dumps({"error": "Failed to store analysis results"}),
                headers={"Content-Type": "application/json"},
                status_code=500
            )
    
    except Exception as e:
        log.error(f"Unexpected error in function handler: {e}")
        log.error(traceback.format_exc())
        return response.Response(
            ctx,
            response_data=json.dumps({"error": "Internal function error"}),
            headers={"Content-Type": "application/json"},
            status_code=500
        )
