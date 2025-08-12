import oci
import oracledb
from flask import Flask, render_template, request, redirect, url_for, flash
import os
from werkzeug.utils import secure_filename
import json
import traceback
from cryptography.hazmat.primitives import serialization

# --- Thick Mode for SODA Support ---
# Initialize thick mode for SODA compatibility with Oracle 23ai
try:
    # Initialize thick mode with Oracle Instant Client
    oracledb.init_oracle_client()
    print("Successfully initialized oracledb thick mode for SODA support")
except Exception as e:
    print(f"Failed to initialize thick mode: {e}")
    print("Note: SODA operations may not be available")

app = Flask(__name__)
app.secret_key = b'_5#y2L"F4Q8z\n\xec]/'

# --- Configuration ---
BUCKET_NAME = "oci-image-analysis-bucket"
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
COLLECTION_NAME = "vision_results"

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- OCI Clients ---
signer = None
object_storage_client = None
namespace = None
db_connection = None

def init_oci_clients():
    """Initialize OCI clients for Object Storage and Database."""
    global signer, object_storage_client, namespace, db_connection
    try:
        print("Attempting to authenticate with OCI Resource Principals...")
        signer = oci.auth.signers.get_resource_principals_signer()
        client_config = {'region': signer.region}
        object_storage_client = oci.object_storage.ObjectStorageClient(config=client_config, signer=signer)
        namespace = object_storage_client.get_namespace().data
        print(f"Object Storage client initialized for namespace: {namespace}")

        # Database connection - try multiple approaches
        print("Attempting database connection...")
        db_connection = None  # Initialize to None
        
        # First try with actual wallet files 
        wallet_location = "/app/wallet"
        if os.path.exists(wallet_location):
            print(f"Found wallet files at {wallet_location}")
            # Check for database password environment variable
            db_password = os.environ.get("DB_PASSWORD", "")
            
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
            db_dsn = os.environ.get("DB_CONNECTION_STRING")
            if not db_dsn:
                print("WARNING: DB_CONNECTION_STRING environment variable is not set and no wallet found. Database features will be disabled.")
                db_connection = None
            else:
                print(f"No wallet found, trying Resource Principals with DSN: {db_dsn}")
                connection_attempts = [
                    ("Resource Principals wallet-based", lambda: oracledb.connect(
                        dsn=db_dsn,
                        config_dir="/dev/null",
                        wallet_location="/dev/null", 
                        wallet_password=""
                    )),
                    ("Basic connection with Resource Principals", lambda: oracledb.connect(dsn=db_dsn))
                ]
        
        # Try all connection attempts
        if 'connection_attempts' in locals():
            for attempt_name, connection_func in connection_attempts:
                try:
                    print(f"Trying {attempt_name}...")
                    db_connection = connection_func()
                    print(f"Successfully connected using {attempt_name}")
                    break
                except Exception as e:
                    print(f"{attempt_name} failed: {e}")
                    continue
        
        if db_connection is None:
            print("WARNING: All database connection attempts failed. Database features will be disabled.")
            print("The application will continue to work for Object Storage operations.")

    except Exception as e:
        print(f"Resource Principals auth failed or DB connection error: {e}. Falling back to local config.")
        try:
            config = oci.config.from_file()
            object_storage_client = oci.object_storage.ObjectStorageClient(config)
            namespace = object_storage_client.get_namespace().data
            print(f"Successfully authenticated with local config for Object Storage.")
            db_connection = None
            flash("Running with local Object Storage config. Database connection is not available in this mode.", "warning")
        except Exception as final_e:
            print(f"!!! FAILED TO INITIALIZE OCI CLIENTS: {final_e} !!!")
            traceback.print_exc()

init_oci_clients()

def get_db_collection():
    """Gets a SODA collection from the database connection."""
    if not db_connection:
        raise Exception("Database connection is not available.")
    soda = db_connection.getSodaDatabase()
    
    # Try to open existing collection first
    try:
        collection = soda.openCollection(COLLECTION_NAME)
        if collection:
            print(f"Using existing SODA collection: {COLLECTION_NAME}")
            return collection
    except Exception as e:
        print(f"Could not open existing collection: {e}")
    
    # Collection doesn't exist or failed to open, create new one
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
        print(f"Created new SODA collection: {COLLECTION_NAME}")
        return collection
    except Exception as create_error:
        # If creation fails due to existing collection with different metadata,
        # try to drop and recreate
        if "ORA-40669" in str(create_error):
            try:
                print(f"Collection exists with different metadata, dropping and recreating...")
                existing_collection = soda.openCollection(COLLECTION_NAME)
                if existing_collection:
                    existing_collection.drop()
                # Now create with new metadata
                collection = soda.createCollection(COLLECTION_NAME, collection_metadata)
                print(f"Successfully recreated SODA collection: {COLLECTION_NAME}")
                return collection
            except Exception as drop_error:
                print(f"Failed to drop and recreate collection: {drop_error}")
        raise create_error

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    images = []
    results_map = {}

    if not object_storage_client:
        flash("OCI Object Storage client not initialized.", "error")
        return render_template('index.html', images=images, results=results_map)

    try:
        list_images_response = object_storage_client.list_objects(namespace, BUCKET_NAME)
        images = [obj.name for obj in list_images_response.data.objects]

        if db_connection:
            collection = get_db_collection()
            print(f"DEBUG: Querying database for results...")
            doc_count = 0
            for doc in collection.find().getDocuments():
                doc_count += 1
                content = doc.getContent()
                print(f"DEBUG: Document {doc_count}: key={doc.key}, content keys={list(content.keys())}")
                if 'filename' in content:
                    filename = content['filename']
                    results_map[filename] = True
                    print(f"DEBUG: Added result for {filename}")
            print(f"DEBUG: Total documents found: {doc_count}, results_map: {results_map}")
        else:
            flash("Database not connected; analysis results are unavailable.", "warning")

    except oci.exceptions.ServiceError as e:
        if e.status == 404:
            flash(f"Upload bucket '{BUCKET_NAME}' not found.", "warning")
        else:
            flash(f"Error listing files from OCI: {e.message}", "error")
    except Exception as e:
        flash(f"An error occurred: {e}", "error")
        traceback.print_exc()

    return render_template('index.html', images=images, results=results_map)

@app.route('/upload', methods=['POST'])
def upload_file():
    if not object_storage_client:
        flash("OCI Object Storage client is not configured.", "error")
        return redirect(url_for('index'))

    if 'file' not in request.files:
        flash('No file part')
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '':
        flash('No selected file')
        return redirect(request.url)

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        try:
            with open(filepath, 'rb') as f:
                object_storage_client.put_object(namespace, BUCKET_NAME, filename, f)
            flash(f'File "{filename}" successfully uploaded. Analysis will begin shortly.')
        except Exception as e:
            flash(f'Error during upload: {e}', "error")
        finally:
            os.remove(filepath)
        return redirect(url_for('index'))
    else:
        flash('File type not allowed.')
        return redirect(request.url)

@app.route('/view/<string:image_filename>')
def view_result(image_filename):
    if not db_connection:
        flash("Database connection is not available.", "error")
        return redirect(url_for('index'))
    
    try:
        collection = get_db_collection()
        print(f"DEBUG: Looking for document with filename={image_filename}")
        doc = collection.find().filter({'filename': image_filename}).getOne()
        if doc:
            print(f"DEBUG: Found document with key={doc.key}")
            json_data = doc.getContent()
            # Clean up the data to remove non-serializable objects like JsonId
            clean_data = json.loads(json.dumps(json_data, default=str))
            return render_template('result.html', filename=image_filename, data=clean_data)
        else:
            print(f"DEBUG: No document found for filename={image_filename}")
            # Check what documents exist
            all_docs = collection.find().getDocuments()
            print(f"DEBUG: Available documents:")
            for d in all_docs:
                content = d.getContent()
                print(f"DEBUG: - key={d.key}, filename={content.get('filename', 'NO_FILENAME')}")
            flash(f"Analysis for {image_filename} not found in the database.", "warning")
            return redirect(url_for('index'))
    except Exception as e:
        flash(f"Could not retrieve analysis for {image_filename}. Error: {e}", "error")
        traceback.print_exc()
        return redirect(url_for('index'))

@app.route('/delete/<string:filename>', methods=['POST'])
def delete_file(filename):
    if not object_storage_client:
        flash("OCI Object Storage client is not configured.", "error")
        return redirect(url_for('index'))

    try:
        object_storage_client.delete_object(namespace, BUCKET_NAME, filename)
        flash(f'File "{filename}" successfully deleted from storage.')
        
        if db_connection:
            collection = get_db_collection()
            print(f"DEBUG: Attempting to delete database entry for {filename}")
            
            # First check if document exists
            doc = collection.find().filter({'filename': filename}).getOne()
            if doc:
                print(f"DEBUG: Found document to delete with key={doc.key}")
                # Try multiple approaches to delete the document
                try:
                    # Method 1: Using collection.find().key().remove()
                    result = collection.find().key(doc.key).remove()
                    print(f"DEBUG: Collection.find().key().remove() result: {result}")
                    
                    # Method 2: Alternative - try dropping the document directly
                    if result == 0:
                        print("DEBUG: First method failed, trying alternative...")
                        # Get the document again and try alternative deletion
                        doc_to_delete = collection.find().key(doc.key).getOne()
                        if doc_to_delete:
                            # Use collection.find().filter() with _id
                            result = collection.find().filter({'_id': doc.key}).remove()
                            print(f"DEBUG: Alternative filter by _id result: {result}")
                    
                    # Explicitly commit the transaction
                    db_connection.commit()
                    print("DEBUG: Database transaction committed")
                    
                    # Verify deletion by trying to find the document again
                    verification_doc = collection.find().key(doc.key).getOne()
                    if verification_doc is None:
                        print("DEBUG: Document successfully deleted (verified)")
                        flash(f'Analysis for "{filename}" successfully deleted from database.')
                    else:
                        print("DEBUG: Document still exists after deletion attempt")
                        flash(f'Warning: Analysis for "{filename}" may not have been completely deleted from database.')
                        
                except Exception as delete_error:
                    print(f"DEBUG: Error during document deletion: {delete_error}")
                    db_connection.rollback()
                    flash(f'Error deleting analysis for "{filename}" from database: {delete_error}')
            else:
                print(f"DEBUG: No document found for filename={filename}")
                flash(f'No analysis found for "{filename}" in database (already deleted or not analyzed).')

    except oci.exceptions.ServiceError as e:
        if e.status != 404:
            flash(f'Error deleting file from OCI Storage: {e.message}', "error")
    except Exception as e:
        flash(f'An error occurred during deletion: {e}', "error")
        traceback.print_exc()

    return redirect(url_for('index'))

@app.route('/debug')
def debug():
    env_vars = dict(os.environ)
    return render_template('debug.html', env_vars=env_vars)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
